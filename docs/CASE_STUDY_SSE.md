# 从 12 秒到 3 秒：一次被数据推翻的流式改造

> 这是一次真实的重构记录。结论是「首 token 延迟从 12–16 秒降到 3–5 秒」，
> 但过程中最重要的三个发现，**都不是我一开始以为的那个**。

## 0. 起因

项目上线后，用户反馈最集中的是「发一句话要等十几秒，然后文字一下子全出来」。

第一反应很清楚：`/chat/send` 是同步接口，一次性返回全文，改成 SSE 流式就能解决。
估算工作量一天，影响面可控。

结果花了三轮才收敛，而且**真正解决问题的改动和这个判断没有关系**。

---

## 1. 一个从未生效的检查

改之前先看前端构建配置：

```json
"build": "tsc && vite build"
```

看起来在类型检查。但 `tsconfig.json` 是 solution 式（只有 `references`、`files: []`），
**裸 `tsc` 没有输入文件，直接退出 0**。

验证方式很直接——故意往 `.vue` 文件里注入一个不存在的变量，`npm run build` 依然成功。
也就是说：

- `npm run build` 从来没做过类型检查
- CI 里那个叫 `Frontend type-check & build` 的 job，**type-check 部分是空转**

改用 `vue-tsc --noEmit -p tsconfig.app.json` 后，暴露出 **13 个类型错误**，其中一个是真正的线上 bug：

```ts
try {
  const progressInterval = setInterval(...)   // 声明在 try 块内
  ...
} catch (error) {
  clearInterval(progressInterval)             // 块级作用域，这里拿到 undefined
  isUploading.value = false
}
```

`const` 是块级作用域。上传失败（401 / 网络错误 / 文件过大）时，`catch` 里会抛
`ReferenceError: progressInterval is not defined`，导致后面两行复位语句**永远不执行**：

- `isUploading` 卡在 `true`，全屏遮罩不消失
- 用户看不到真正的错误原因
- 无法再次上传，只能刷新页面

**这个 bug 之所以能活到上线，正是因为构建从来没有真正检查过类型。**

> 教训一：CI 里那个绿色的勾，要先验证它到底检查了什么。
> 一个「退出码 0」的检查步骤，可能什么都没检查。

---

## 2. 接上 SSE，然后发现没有用

后端加 `/chat/stream`，用 LangGraph 的 `stream_mode=["messages","values"]` 推 token。
技术上有三个前提需要先验证，实测结果如下：

| 假设 | 实测结果 |
| --- | --- |
| 节点用 `model.invoke()` 拿不到 token 流，需要改成 `stream` | **错**。`invoke` 在流式上下文里会走 callback，390 个事件、14 个非空 |
| 路由决策的 LLM 调用会混进正文 | **对**，需要 `config={"tags": ["route_decision"]}` 过滤 |
| 事件流里每个 chunk 都有内容 | **错**。398 个事件里非空只有 14 个，必须 `if text: continue`，否则一个答案往前端推几百个空事件 |

第一条推翻了我的预判，好处是**四个节点的 LLM 调用方式一行都不用改**。

前端接上后，测出来的数据是：

| 指标 | 改前 | 改后 |
| --- | --- | --- |
| 首 token 时间（TTFT） | 12–16 s | 14.16 s / 12.31 s |
| **总耗时 − TTFT** | — | **0.44 s / 3.12 s** |

第三行是关键。**首 token 到达之后，整段文字在 0.5 秒内就吐完了。**

用户的实际感受是「等 14 秒 → 半秒内全部出现」，和改造前「等 14 秒 → 整段出现」几乎没有区别。
SSE 只是把最后那半秒铺开了，**真正等的时间一点没少。**

> 教训二：流式改造要看的不是 TTFT 有没有变小，而是
> **TTFT 和总耗时的差值有没有拉开**。差值小说明瓶颈根本不在传输方式上。

---

## 3. 真正的瓶颈：模型在「思考」时不产出 token

`qwen3.5-plus` 默认开启 thinking。思考阶段不产出 `content` token，而且
`reasoning_content` 也没有放在 chunk 的 `additional_kwargs` 里（实测拿到 0 个 reasoning 块），
前端连「思考中」都没得显示，只能纯等。

做了一组对照实验，只改 `extra_body`：

| 配置 | TTFT | 总耗时 |
| --- | --- | --- |
| 默认（开启 thinking） | 13.95 s | 14.66 s |
| `enable_thinking=False` | **0.63 s** | 1.80 s |

**22 倍。**

但关掉 thinking 有个风险：医疗问答关掉思考可能掉推理质量。所以第一轮只关**路由**那一次调用——
路由只需要输出一个单词（`attending_doctor` / `pharmacist` / `medical_examiner` / `supervisor`），
思考对它没有任何帮助，纯赚 9–10 秒。

为了确认这不是用准确率换速度，跑了 20 次对照：

| 场景 | 关 thinking | 开 thinking |
| --- | --- | --- |
| 问血糖 | `attending_doctor` ×2 | `attending_doctor` ×2 |
| 寒暄后问血糖 | `attending_doctor` ×2 | `attending_doctor` ×2 |
| 刚上传要求整理 | `medical_examiner` ×2 | `medical_examiner` ×2 |
| 问药 | `pharmacist` ×2 | `pharmacist` ×2 |
| 描述头晕 | `medical_examiner` ×2 | `medical_examiner` ×2 |
| **平均耗时** | **1.62 s** | **15.01 s** |

**20/20 判定完全一致，耗时快 9.3 倍。**

正文节点保留 thinking——那是需要推理的地方，质量优先。

> 教训三：先量清楚时间花在哪。这次 70% 的等待来自一个和网络、和框架、
> 和传输协议都无关的地方：**模型在产出第一个字之前思考了 13 秒**。

---

## 4. 流式一上线，三个被掩盖的问题同时暴露

### 4.1 「承诺转接」但转不出去

图结构里 `medical_examiner → END` 是**固定边**，只有 supervisor 用了条件边。
所以体检员节点返回的 `next` 字段**从来没人读**。

原先体检员遇到「报告里的数值是多少」这类问题时，会说「无法查询」。
改成「这部分由主治医生为您解读」之后，如果 LLM 恰好把它判给体检员，
用户会收到一句承诺，**然后本轮直接结束**，必须再问一次才有下文。

修法：给体检员改条件边 + 一个纯关键词函数 `wants_report_value()`，
命中时直接返回 `next="attending_doctor"` 并早返回。

验证时特意做了整图链路测试（不只是直接调节点函数），因为早返回只塞了一条 `AIMessage`，
医生节点拿到的最后一条消息是 AI 而不是 human——这是个真实风险点：

```
[0] human: 我报告里的空腹血糖是多少
[1] ai:    【体检员】这部分我请主治医生为您解读，正在为您转接
[2] ai:    【主治医生】您的空腹血糖为 6.2 mmol/L（依据《报告》第 2 页）
```

`messages` 走 `add_messages`，早返回只追加不覆盖历史，医生能正常作答。

### 4.2 角色标签从来没生效过

流式事件里带了 `meta`，本想用 `meta["langgraph_node"]` 映射角色名。实测：

```
第1个token: node=supervisor | path=('__pregel_pull','supervisor') | tags=['route_decision']
第3个token: node=model      | path=('__pregel_pull','model')      | tags=None
```

正文 token 全部来自 `create_agent()` 的**内部子图**，节点名是 `model`，
把外层节点名完全覆盖。`langgraph_node`、`langgraph_path`、`checkpoint_ns` **三个字段都只剩 `model`**。

想用 `values` 模式的 `next` 提前预判也不行——`values` 是**节点结束后**才推送，
那时正文已经流完了。

修法：在 4 个节点入口用 `get_stream_writer()` 主动发自定义事件：

```python
try:
    get_stream_writer()({"type": "agent", "agent": "attending_doctor"})
except Exception:
    pass   # /chat/send 不走 stream，没有 writer，必须包住
```

这个 `try/except` 是必须的——非流式接口里 `get_stream_writer()` 会抛异常，
不包住会让 `/chat/send` 直接 500。

### 4.3 流式分析藏在全屏遮罩后面

上传 PDF 后，前端会调一次分析接口。改动接上了流式，测试也通过了（DOM 里的文字确实在逐步增长）。

但采样数据暴露了问题：

```
首个 token：     t=116.66 s，长度 8
遮罩消失：       t=117.00 s，长度 39
最终长度：       t=203    （t=119.10 s）
```

文字长了 164 字、用了 2.1 秒——**全在 `z-index: 9999`、70% 黑 + 5px 模糊的全屏遮罩后面发生**。
用户看到的是「等 80 多秒 → 遮罩消失 → 整段文字已经在那儿了」，和改造前一模一样。

修法是首个 token 到达时就关遮罩：

```ts
let firstToken = true
const handlers = {
  onToken: (text) => {
    if (firstToken) {
      firstToken = false
      isUploading.value = false     // 让用户看见文字在长
      uploadProgress.value = 100
    }
    ...
  }
}
```

**验收标准改成 `遮罩消失时的字符数 < 最终字符数`**，并要求给出两个具体数字。
如果遮罩一直盖到结束，这两个数字会相等——这个判据能一句话区分「真的可见」和「只是在 DOM 里变了」。

> 教训四：验证「流式生效」不能只看 DOM 文本长度在变。
> 要问一句：**用户这一刻看得见吗？**

---

## 5. 一个顺手做掉的正确性验证

前端 `TextDecoder` 处理 SSE 时，一个中文字符可能被 HTTP chunk 切断（UTF-8 三字节）。
代码里用了 `{stream: true}` + 保留半包（`parts.pop()`），但这不是靠读代码能确认的。

把真实的 `streamChat.ts` 用 esbuild 转译成 mjs，在 Node 里 mock `fetch`，
返回**逐字节分片**的流：

| 分片方式 | 结果 |
| --- | --- |
| 1 字节 / 块 | 29/29 字符一致，**U+FFFD = 0** |
| 2 字节 / 3 字节 / 整包 | 同样通过 |
| 含 4 字节 emoji `😊` | 逐字节分片也正确，代理对处理无误 |
| 混入非法行 `data: 这不是json` | 跳过该行，后续 token 正常，流不崩 |
| HTTP 500 | 正确抛错，交给上层转错误态 |

比在浏览器 DevTools 里手动限速更严格，而且可重复。

---

## 6. 最终结果

| 指标 | 改造前 | 改造后 |
| --- | --- | --- |
| 首 token 延迟 | 12–16 s | **3–5 s** |
| 用户可见输出 | 0 直到结束 | 持续增长 |
| 路由决策耗时 | 15.01 s（均） | 1.62 s（均） |
| 路由判定准确率 | 20/20 | 20/20（无回归） |
| 前端类型检查 | 从未执行 | `vue-tsc` 0 error，已接入 CI |
| 上传失败行为 | 界面卡死 + `ReferenceError` | 正常复位并提示 |

首 token 3–5 秒的构成：路由 1.2–2.3 s（已关 thinking）+ 正文首 token 1.7–3.9 s（thinking 保留）。

要再往下压只能关正文的 thinking，预计能到 1–2 秒，但医疗问答的推理质量需要
用真实问题 A/B 之后才能决定——**这个取舍没有替用户做**。

---

## 7. 可迁移的部分

1. **先验证「检查」本身有效。** `tsc` 空转了不知道多久，直到故意注入错误才发现。
   任何「应该拦住问题」的环节，都值得故意制造一次失败来确认它真的会拦。
2. **流式看差值，不看绝对值。** TTFT 降了但总耗时没降，说明瓶颈不在传输层。
3. **把等待拆成段，逐段量。** 路由 10 s + 正文 2 s 和 正文 12 s，优化手段完全不同。
4. **验收标准要包含「人能感知到」。** DOM 变了 ≠ 用户看见了。遮罩、loading、动画
   都可能把「变化」挡在外面。
5. **框架的元信息不一定是你要的那一层。** `create_agent` 内部子图会覆盖 `langgraph_node`，
   需要在自己的节点里主动发事件。

## 8. 走错的地方（留着提醒自己）

- 一开始判断「用 `model.invoke()` 拿不到 token 流」，实测是错的，白准备了一套改 4 个节点的方案。
- 第一次验收 SSE 时只看了「有 token 事件、TTFT 变小」，没看总耗时，差点把无效改造判成功。
- S3 那轮验收只测了「DOM 文本在增长」，没测「用户能不能看见」，导致一轮返工。
- 复验时踩了三次同一个坑：**改了代码但服务没重启，测的是旧代码**。
  后来固定流程：先比对「服务进程启动时间」和「代码文件 mtime」，再下结论。

---

## 附：关键数据一览

```
路由决策（关 thinking）：1.22 / 2.32 s
路由决策（开 thinking）：10.53 / 24.62 s
thinking 对照（正文）： 13.95 s → 0.63 s（TTFT）
stream 事件非空率：     14 / 398
逐字节解码正确率：      29/29，U+FFFD = 0
遮罩消失时长度：        39 / 最终 203
```
