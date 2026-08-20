"""HR 考勤 MCP 工具；只允许按已验签的钉钉身份查询本人数据。"""

from __future__ import annotations

from datetime import date
from typing import Any

from enterprise_mcp.auth import require_dingtalk_user_id
from enterprise_mcp.hr_client import get_hr_client
from enterprise_mcp.server import GovernedFastMCP


def register_hr_tools(mcp: GovernedFastMCP) -> None:
    """向 MCP Server 注册当前钉钉用户本人的考勤查询工具。"""

    @mcp.tool(name="hr_attendance_sign_records", structured_output=True)
    async def hr_attendance_sign_records(
        start_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        """查询当前钉钉用户在日期范围内的原始打卡记录，起止日期均包含。"""

        _, user_id = require_dingtalk_user_id()
        params = _build_attendance_params(user_id, start_date, end_date)
        data = await get_hr_client().get("/attendance/sign-records", params)
        return {"type": "hr_attendance_sign_records", "items": data}

    @mcp.tool(name="hr_attendance_daily_detail", structured_output=True)
    async def hr_attendance_daily_detail(
        start_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        """查询当前钉钉用户在日期范围内按日计算的考勤明细，起止日期均包含。"""

        _, user_id = require_dingtalk_user_id()
        params = _build_attendance_params(user_id, start_date, end_date)
        data = await get_hr_client().get("/attendance/daily-detail", params)
        return {"type": "hr_attendance_daily_detail", "items": data}

    @mcp.tool(name="hr_attendance_summary", structured_output=True)
    async def hr_attendance_summary(
        start_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        """查询当前钉钉用户在日期范围内的加班、请假和出差/外出汇总。"""

        _, user_id = require_dingtalk_user_id()
        params = _build_attendance_params(user_id, start_date, end_date)
        data = await get_hr_client().get("/attendance/summary", params)
        return {"type": "hr_attendance_summary", "summary": data}


def _build_attendance_params(user_id: str, start_date: str, end_date: str) -> dict[str, str]:
    """校验包含边界的日期范围，并构造 HR API 查询参数。"""

    try:
        parsed_start = date.fromisoformat(start_date)
        parsed_end = date.fromisoformat(end_date)
    except (TypeError, ValueError) as exc:
        raise ValueError("start_date 和 end_date 必须使用 yyyy-MM-dd 格式") from exc

    if parsed_start.isoformat() != start_date or parsed_end.isoformat() != end_date:
        raise ValueError("start_date 和 end_date 必须使用 yyyy-MM-dd 格式")
    if parsed_start > parsed_end:
        raise ValueError("start_date 不能晚于 end_date")

    return {
        "ftalkId": user_id,
        "startDate": start_date,
        "endDate": end_date,
    }
