"""用户偏好 API 模块

偏好数据写入 LangGraph 长记忆（PostgresStore）的 ``user_preferences``
命名空间，与多智能体对话共用同一份存储，因此在这里保存的偏好
（回答风格、过敏史等）会被 supervisor / 体检员在后续对话中读取到。

接口：
- POST /preferences/save  保存或覆盖单个偏好
- GET  /preferences/list  读取当前用户的全部偏好
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.agents.memory import get_long_term_memory
from app.models.user import User

router = APIRouter()

# 长记忆命名空间（与 app/agents/tools.py 保持一致）
PREF_NAMESPACE = "user_preferences"


class PreferenceSaveRequest(BaseModel):
    """保存偏好请求体"""

    user_id: str = Field(
        default="",
        description="长记忆用户 ID，格式 user_{id}；留空时由登录态推导",
    )
    key: str = Field(..., min_length=1, max_length=64, description="偏好键，如 preferred_style")
    value: str = Field(default="", max_length=2000, description="偏好值")


def _resolve_user_id(requested: str, current_user: User) -> str:
    """把请求里的 user_id 与登录态对齐，防止读到别人的偏好"""
    actual = f"user_{current_user.id}"
    if requested and requested != actual:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="无权访问其他用户的数据",
        )
    return actual


@router.post("/save")
def save_preference(
    payload: PreferenceSaveRequest,
    current_user: User = Depends(get_current_user),
):
    """保存或覆盖一个偏好项（同 key 会覆盖旧值）"""
    user_id = _resolve_user_id(payload.user_id, current_user)
    store = get_long_term_memory()

    try:
        store.put(
            (PREF_NAMESPACE, user_id),
            f"pref_{payload.key}",
            {"key": payload.key, "value": payload.value},
        )
    except Exception as exc:  # pragma: no cover - 依赖外部存储
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"保存偏好失败: {exc}",
        ) from exc

    return {"success": True, "key": payload.key, "value": payload.value}


@router.get("/list")
def list_preferences(current_user: User = Depends(get_current_user)):
    """读取当前用户的全部偏好，按 ``key: value`` 逐行返回"""
    store = get_long_term_memory()
    user_id = f"user_{current_user.id}"

    try:
        items = store.search((PREF_NAMESPACE, user_id))
    except Exception as exc:  # pragma: no cover - 依赖外部存储
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"读取偏好失败: {exc}",
        ) from exc

    # 同一个 key 可能因为历史写入存在多条，取最后写入的那条
    latest: dict[str, dict] = {}
    for item in items:
        key = (item.value or {}).get("key")
        if key:
            latest[key] = item.value

    lines = [
        f"{data.get('key')}: {data.get('value', '')}"
        for data in latest.values()
    ]

    return {"user_id": user_id, "preferences": "\n".join(lines)}
