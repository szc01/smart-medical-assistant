# P0-1 评测结果 2026-10-19 19:44

| 类别 | 通过/总数 | 通过率 | 失败项 |
| --- | --- | --- | --- |
| C 无关不硬扯 | 6/6 | 100.0% | - |
| **合计** | **6/6** | **100.0%** | 耗时 1m15s |

## 失败详情

无。

## 说明

- B 类先调用 `save_extracted_info_to_store` 写入 basic_info/symptoms/medical_history/allergies/test_results，再用 `index_pages` 建两页索引；仍未覆盖「上传 → PDF 解析 → 索引」链路。
- B4/B8 只判数值（路由到 medical_examiner / supervisor，不检索，不要求页码）；B5/B6/B7 保留页码要求。
- D 类主判据只看报告特征值；无数据表述仅作备注，不参与判定。
- A 类每题独立线程；B 类共享 A 的会话；C/D 每题独立线程；E 使用 A 的会话。
- 判定为字符串包含/边界正则，未使用 LLM 裁判。
- 清理校验（evals）：users=0 conversations=0 medical_documents=0 chroma=1 uploads=0。
