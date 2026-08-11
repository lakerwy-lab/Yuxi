---
name: meeting-room
slug: meeting-room
description: "通过钉钉 API 查询和预订会议室；只向用户请求一次选择/确认。"
---

# 钉钉会议室

用于查询、预览、预订、取消钉钉会议室。

## 预订流程

1. 将用户给出的相对日期和时间转换为带 `+08:00` 的 ISO 8601 时间；不确定时先询问必要信息。
2. 调用 `search_meeting_rooms`，向用户展示可用会议室、时间和冲突信息。
3. 调用 `preview_booking` 生成确认令牌。
4. 只调用一次 `ask_user_question`，让用户选择会议室并确认这一次预订。
5. 用户确认后直接调用 `confirm_booking`。它会重新检查空闲状态、创建日程、预订会议室并在失败时执行补偿，不得再次向用户弹出确认。

取消或查询历史预订分别使用 `cancel_booking` 和 `my_bookings`。

只读 SQL 工具不属于本 Skill。
