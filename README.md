# 公告

请不要魔改本项目多线程刷论坛，本项目的初衷只是为了给个人账号刷一下访问天数的，不是给号商养号用的。

# LinuxDo 每日签到（每日打卡）

## 项目描述

这个项目用于自动登录 [LinuxDo](https://linux.do/) 网站并随机读取几个帖子。它使用 Python 和 Playwright
自动化库模拟浏览器登录并浏览帖子，以达到自动签到的功能。

有时会登录失败，重试一下就行了，嫌失败邮件通知烦的可以吧action的邮件通知关了

## 本 Fork 的增强

- **免维护登录**：账号密码登录为主路线（Secrets 永不过期），在浏览器内填表提交，
  可承载 Cloudflare 挑战；**不再需要每次运行前重新抓 Cookie 填变量**。
- **Turnstile 自动尝试**：遇到 Cloudflare 人机验证时，自动定位验证框并模拟点击复选框（尽力而为）。
- **失败现场留证**：登录失败时自动保存截图 + HTML 到 `debug/`，Actions 运行结束后作为
  artifact 上传（`debug-screenshots`），不用再猜"到底卡在哪一步"。
- **LDC 积分站查询**：签到后自动调用 `credit.linux.do` 的 `/api/v1/oauth/user-info` 接口，
  上报「可用余额 / 社区余额 / 待结算余额 / 信任等级」。该接口已对照
  [linux-do/credit](https://github.com/linux-do/credit) 源码核实（GET 请求无需 CSRF 头）。
  会话获取优先走浏览器自动 OAuth（linux.do 已登录时通常自动完成），也可用
  `LINUXDO_CREDIT_COOKIES` 手动提供 Cookie 兜底。
- **登录判定修复**：原版 `"avatar" in html` 的宽松判定在未登录时也会命中（Discourse 页面
  JS/CSS 里含大量 avatar 字符串），导致误报登录成功；现改为 `/session/current.json` 同步
  XHR + 头部用户元素双重判定。
- **修复主题数不足崩溃**：`random.sample` 在主题少于 10 篇时会抛异常，已改为按实际数量抽样。
- **CF 挑战识别修复**：linux.do 正常页面（约 800KB HTML）内嵌了 challenge-platform 脚本
  引用，按 HTML 关键字全文匹配会把正常页误判为挑战页；已改为仅对小页面（< 50KB）生效。
- **点赞默认关闭**：社区已取消点赞积分奖励，脚本点赞存在被判定异常行为的风险，
  新增 `LIKE_ENABLED` 开关，默认 `false`。如需开启请自行承担风险。
- **关闭上游自动同步**：因已深度定制，`sync.yml` 的定时同步已关闭（保留手动触发），
  避免上游更新覆盖定制内容。

## 功能

- 自动登录`LinuxDo`。
- 自动浏览帖子。
- 自动查询 `credit.linux.do` LDC 积分余额（可选）。
- 每天在`GitHub Actions`中自动运行。
- 支持`青龙面板` 和 `Github Actions` 自动运行。
- (可选)`Telegram`通知功能，推送获取签到结果（目前只支持GitHub Actions方式）。
- (可选)`Gotify`通知功能，推送获取签到结果。
- (可选)`Server酱³`通知功能，推送获取签到结果。
- (可选)`wxpush`通知功能，推送获取签到结果。
## 环境变量配置

### 登录方式

**推荐：账号密码登录（免维护，不再需要反复抓 Cookie）**

| 环境变量名称             | 描述                | 示例值                                |
|--------------------|-------------------|------------------------------------|
| `LINUXDO_USERNAME` | 你的 LinuxDo 用户名或邮箱 | `your_username` 或 `your@email.com` |
| `LINUXDO_PASSWORD` | 你的 LinuxDo 密码     | `your_password`                    |

> 为什么不再推荐 Cookie：`cf_clearance` 绑定 IP（你本地抓的 Cookie 到 GitHub Actions 换了个 IP 就失效），且寿命只有几十分钟；
> 真正长效的只有 `_t`，但极易漏复制或被登出。账号密码放在 GitHub Secrets 里永不过期（除非你自己改密码），登录在浏览器内完成，可承载 Cloudflare 挑战。
> **配置一次账号密码即可，之后无需任何维护。**

**可选：Cookie 登录（仅在不想提供密码时使用）**

| 环境变量名称             | 描述                                         | 示例值                          |
|--------------------|--------------------------------------------|------------------------------|
| `LINUXDO_COOKIES`  | 从浏览器 DevTools 复制的 Cookie 字符串        | `_t=xxx; _forum_session=yyy` |

> 获取方式：打开 [linux.do](https://linux.do/) 并登录 → 按 F12 → Application → Cookies → `https://linux.do` → **务必包含 `_t`**（长效登录凭证），复制为字符串粘贴。
> 只有 `_forum_session` 等临时 Cookie 是登不上的。

> 若同时设置了账号密码和 Cookie，**账号密码优先**；密码登录失败时自动回退尝试 Cookie。

**LDC 积分站（可选）**

| 环境变量名称                  | 描述                                          | 示例值                        |
|-------------------------|---------------------------------------------|----------------------------|
| `LINUXDO_CREDIT_COOKIES`| `credit.linux.do` 的 Cookie 字符串（兜底用，一般可省略） | `session=xxx`              |

> 获取方式：浏览器登录 [credit.linux.do](https://credit.linux.do/home) → F12 → Application → Cookies → `https://credit.linux.do` → 复制为 `name=value; name2=value2` 格式。
> 不配置时脚本会尝试用 linux.do 登录态自动完成 OAuth。

### 可选变量

| 环境变量名称                | 描述                   | 示例值                                    |
|----------------------|----------------------|----------------------------------------|
| `BROWSE_ENABLED`     | 是否启用浏览帖子功能           | `true` 或 `false`，默认为 `true`           |
| `BROWSE_TOPIC_COUNT` | 每次浏览的主题帖数量上限         | `1`-`20`，默认为 `10`                     |
| `LIKE_ENABLED`       | 是否启用自动点赞（**默认关闭**，有风险） | `true` 或 `false`，默认为 `false`          |
| `LDC_ENABLED`        | 是否查询 LDC 积分站余额       | `true` 或 `false`，默认为 `true`           |
| `GOTIFY_URL`         | Gotify 服务器地址         | `https://your.gotify.server:8080`      |
| `GOTIFY_TOKEN`       | Gotify 应用的 API Token | `your_application_token`               |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token   | `123456789:ABCdefghijklmnopqrstuvwxyz` |
| `TELEGRAM_CHAT_ID`   | Telegram 用户 ID       | `123456789`                            |
| `SC3_PUSH_KEY`       | Server酱³ SendKey     | `sctpxxxxt`                            |
| `WXPUSH_URL`         | wxpush 服务器地址         | `https://your.wxpush.server`           |
| `WXPUSH_TOKEN`       | wxpush 的 token       | `your_wxpush_token`                    |

---

## 如何使用

### GitHub Actions 自动运行

此项目的 GitHub Actions 配置会自动每天运行2次签到脚本。你无需进行任何操作即可启动此自动化任务。GitHub Actions 的工作流文件位于 `.github/workflows` 目录下，文件名为 `daily-check-in.yml`。

#### 配置步骤

1. **设置环境变量**：
    - 在 GitHub 仓库的 `Settings` -> `Secrets and variables` -> `Actions` 中添加以下变量：
        - （二选一）`LINUXDO_COOKIES`：从浏览器复制的 Cookie 字符串（**推荐，优先使用**）。
        - （二选一）`LINUXDO_USERNAME` + `LINUXDO_PASSWORD`：你的 LinuxDo 用户名/邮箱和密码。
        - (可选) `BROWSE_ENABLED`：是否启用浏览帖子，`true` 或 `false`，默认为 `true`。
        - (可选) `GOTIFY_URL` 和 `GOTIFY_TOKEN`。
        - (可选) `SC3_PUSH_KEY`。
        - (可选) `WXPUSH_URL` 和 `WXPUSH_TOKEN`。
        - (可选) `TELEGRAM_BOT_TOKEN` 和 `TELEGRAM_CHAT_ID`。

2. **手动触发工作流**：
    - 进入 GitHub 仓库的 `Actions` 选项卡。
    - 选择你想运行的工作流。
    - 点击 `Run workflow` 按钮，选择分支，然后点击 `Run workflow` 以启动工作流。

#### 运行结果

##### 网页中查看

`Actions`栏 -> 点击最新的`Daily Check-in` workflow run -> `run_script` -> `Execute script`

可看到`Connect Info`：
（新号可能这里为空，多挂几天就有了）
![image](https://github.com/user-attachments/assets/853549a5-b11d-4d5a-9284-7ad2f8ea698b)

### 青龙面板使用

*注意：如果是docker容器创建的青龙，**请使用`whyour/qinglong:debian`镜像**，latest（alpine）版本可能无法安装部分依赖*

1. **依赖安装**
    - 安装Python依赖
      - 进入青龙面板 -> 依赖管理 -> 安装依赖
        - 依赖类型选择`python3`
        - 自动拆分选择`是`
        - 名称填写(仓库`requirements.txt`文件的完整内容)：
            ```
            DrissionPage==4.1.0.18
            wcwidth==0.2.13
            tabulate==0.9.0
            loguru==0.7.2
            curl-cffi
            bs4
            ```
        - 点击确定
    - 安装 linux chromium 依赖
      - 青龙面板 -> 依赖管理 -> 安装Linux依赖
      - 名称填`chromium`
  
        > 若安装失败，可能需要执行`apt update`更新索引（若使用docker则需进入docker容器执行）


2. **添加仓库**
    - 进入青龙面板 -> 订阅管理 -> 创建订阅
    - 依次在对应的字段填入内容（未提及的不填）：
      - **名称**：Linux.DO 签到
      - **类型**：公开仓库
      - **链接**：https://github.com/doveppp/linuxdo-checkin.git
      - **分支**：main
      - **定时类型**：`crontab`
      - **定时规则**(拉取上游代码的时间，一天一次，可以自由调整频率): 0 0 * * *

3. **配置环境变量**
    - 进入青龙面板 -> 环境变量 -> 创建变量
    - 需要配置以下变量：
        - （二选一）`LINUXDO_COOKIES`：从浏览器复制的 Cookie 字符串（**推荐，优先使用**）
        - （二选一）`LINUXDO_USERNAME`：你的LinuxDo用户名/邮箱
        - （二选一）`LINUXDO_PASSWORD`：你的LinuxDo密码
        - (可选) `BROWSE_ENABLED`：是否启用浏览帖子功能，`true` 或 `false`，默认为 `true`
        - (可选) `GOTIFY_URL`：Gotify服务器地址
        - (可选) `GOTIFY_TOKEN`：Gotify应用Token
        - (可选) `SC3_PUSH_KEY`：Server酱³ SendKey
        - (可选) `WXPUSH_URL`：wxpush服务器地址
        - (可选) `WXPUSH_TOKEN`：wxpush的token
        - (可选) `TELEGRAM_BOT_TOKEN`：Telegram Bot Token
        - (可选) `TELEGRAM_CHAT_ID`：Telegram用户ID

4. **手动拉取脚本**
    - 首次添加仓库后不会立即拉取脚本，需要等待到定时任务触发，当然可以手动触发拉取
    - 点击右侧"运行"按钮可手动执行

#### 运行结果

##### 青龙面板中查看
- 进入青龙面板 -> 定时任务 -> 找到`Linux.DO 签到` -> 点击右侧的`日志`

### Gotify 通知

当配置了 `GOTIFY_URL` 和 `GOTIFY_TOKEN` 时，签到结果会通过 Gotify 推送通知。
具体 Gotify 配置方法请参考 [Gotify 官方文档](https://gotify.net/docs/).

### Server酱³ 通知

当配置了 `SC3_PUSH_KEY` 时，签到结果会通过 Server酱³ 推送通知。
获取 SendKey：请访问 [Server酱³ SendKey获取](https://sc3.ft07.com/sendkey) 获取你的推送密钥。

### wxpush 通知

当配置了 `WXPUSH_URL` 和 `WXPUSH_TOKEN` 时，签到结果会通过 wxpush 推送通知。
使用 POST 方式推送，请求地址为 `{WXPUSH_URL}/wxsend`。

### Telegram 通知

可选功能：配置 Telegram 通知，实时获取签到结果。

需要在 GitHub Secrets 中配置：
- `TELEGRAM_BOT_TOKEN`：Telegram Bot Token
- `TELEGRAM_CHAT_ID`：Telegram 用户 ID

获取方法：
1. Bot Token：与 [@BotFather](https://t.me/BotFather) 对话创建机器人获取
2. 用户 ID：与 [@userinfobot](https://t.me/userinfobot) 对话获取

未配置时将自动跳过通知功能，不影响签到。


## 自动更新

- **Github Actions**：默认状态下自动更新是关闭的，[点击此处](https://github.com/ChatGPTNextWeb/ChatGPT-Next-Web/blob/main/README_CN.md#%E6%89%93%E5%BC%80%E8%87%AA%E5%8A%A8%E6%9B%B4%E6%96%B0)
查看打开自动更新步骤。
- **青龙面板**：更新是以仓库设置的定时规则有关，按照本文配置，则是每天0点更新一次。


