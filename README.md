# AstrBot AtCoder 比赛助手

插件从 AtCoder 官网读取比赛安排和公开赛后结果。默认关注 ABC、ARC、AGC，消息时间统一用北京时间。

## 安装和配置

1. 将本仓库克隆到 AstrBot 实例的 `data/plugins/astrbot_plugin_atcoder_helper`，在实例的 Python 环境中安装仓库 `requirements.txt` 里的依赖，然后重启 AstrBot。
2. 在接收推送的群或私聊发送 `/sid`，复制返回的完整 **UMO**。在 AstrBot 的插件配置里把它加入 `targets`，每个会话一项。主动消息需要平台支持 `Context.send_message`，例如 QQ 的 OneBot v11 接入。 [AstrBot 消息发送文档](https://docs.astrbot.app/dev/star/guides/send-message.html)
3. 在 `players` 中每项填写一个 AtCoder 用户名，例如 `tourist`。所有目标会话共用这份名单。可在 `series` 中选择 `abc`、`arc`、`agc`，然后打开 `enabled`，保存并重载插件。名单留空时仍可推送赛程、报名和开赛消息。
4. 在目标会话发送 `/atcoder list` 检查赛历，发送 `/atcoder results abc482` 检查已结束场次的成员结果。使用自定义唤醒前缀时，把示例中的 `/` 换成实际前缀。

## 自动推送

| 消息 | 什么时候发 |
| --- | --- |
| 赛程 | 轮询发现 7 天内开赛的比赛时，发比赛页、北京时间和 Rated 范围。 |
| 报名提醒 | 开赛前 24 小时内，官网比赛页出现可用报名按钮时发一次。成员登录官网自行报名，机器人无需 AtCoder 账号。 |
| 开赛通知 | 到开赛时间后发出。错过开赛时刻时，最多在开赛后 10 分钟内补发。 |
| 赛后总结 | 赛后 20 分钟开始检查，官网发布有效名次后发出，最长自动重试到赛后 24 小时。显示名单中有名次成员的名次、Rating 涨跌和 Performance，未计 Rating 的成员单独标注。 |

插件约每 30 秒检查是否到了推送时机，赛历和赛后结果各缓存 5 分钟。发送成功后按会话、比赛及消息类型保存记录，重载后继续去重。发送失败会在对应时间窗口内重试。首次开启时，已进入时间窗口的场次也可能推送。

赛后统计依据 AtCoder 的公开结果，缺少名次的成员统一显示「结果页无名次」。这份数据没有逐题通过记录，报名提醒也没有个人报名状态。 [官网赛历](https://atcoder.jp/contests) · [公开结果示例](https://atcoder.jp/contests/abc482/results/json)

## 聊天指令

| 指令 | 作用 |
| --- | --- |
| `/atcoder help` | 查看指令帮助。 |
| `/atcoder list` | 查看所选系列中最近 5 场即将开始的比赛。 |
| `/atcoder results <比赛 ID>` | 手动查指定比赛的关注成员结果。自动推送窗口过去后仍可查询。 |
