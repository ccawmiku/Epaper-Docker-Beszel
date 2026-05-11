# ESP32-C3 墨水屏 NAS 状态屏

版本：`0.1.1`

这个项目把 ESP32-C3 作为纯显示端使用：NAS 上的 Docker 服务负责连接 Beszel、渲染 400x300 的墨水屏画面，并输出统一的 `frame.bin`。浏览器预览页和 ESP32-C3 都读取同一个 `frame.bin`，所以网页预览和真实墨水屏显示尽量保持一致。

## 功能

- 通过 Beszel API 获取 NAS、路由器和 Docker 容器状态
- 服务端渲染 400x300 黑/红双色帧
- `/preview` 后台预览页可调整：
  - 墨水屏刷新间隔
  - 折线图时间窗口，最长 24 小时
  - 强制刷新
- ESP32-C3 支持 WiFi、Arduino OTA、定时拉取帧
- GitHub Actions 自动构建 GHCR Docker 镜像

## 目录

- `server/`：Python FastAPI 服务
- `esp32c3_epaper_frame/`：ESP32-C3 Arduino 固件
- `EPaper_Direct_Test/`：无图库全刷屏幕测试
- `.github/workflows/docker.yml`：Docker 镜像构建和发布

## 本地运行

```powershell
cd server
copy .env.example .env
pip install -r requirements.txt
python app.py
```

编辑 `server/.env`：

```env
BESZEL_BASE_URL=http://YOUR_NAS_IP:8090
BESZEL_EMAIL=你的 Beszel 邮箱
BESZEL_PASSWORD=你的 Beszel 密码
BESZEL_HISTORY_MINUTES=30
BESZEL_RECORD_TYPE=1m
DISPLAY_CHART_MINUTES=1440
EPAPER_FONT_PATH=/fonts/comic.ttf
APP_HOST=0.0.0.0
APP_PORT=15001
```

字体现在优先使用 Comic Sans。Windows/PyCharm 本地会自动读取 `C:\Windows\Fonts\comicbd.ttf`；Docker 里把 `comic.ttf` 放到 `server/fonts/comic.ttf`，上面的 `EPAPER_FONT_PATH=/fonts/comic.ttf` 就会生效。

打开后台预览：

```text
http://127.0.0.1:15001/preview
```

主要接口：

- `GET /preview`：后台预览和控制页
- `GET /frame.bin`：ESP 读取的二进制帧
- `GET /screen.png`：调试 PNG
- `GET /api/display/data`：渲染使用的数据
- `GET /api/device/config`：ESP 读取刷新间隔和强制刷新序号
- `POST /api/admin/settings`：保存后台设置
- `POST /api/admin/force-refresh`：强制刷新

## Docker 运行

本地构建：

```bash
cd server
cp .env.example .env
docker compose up -d --build
```

端口：

- `15001`：后台预览和 API
- `15002`：ESP 读取同一服务，映射到容器内 `15001`

## Pull 镜像运行

推送到 GitHub 后，Actions 会发布：

```text
ghcr.io/ccawmiku/epaper-nas-display:0.1.1
ghcr.io/ccawmiku/epaper-nas-display:latest
```

NAS 上运行：

```bash
cd server
cp .env.example .env
export EPAPER_IMAGE=ghcr.io/ccawmiku/epaper-nas-display:0.1.1
docker compose -f docker-compose.image.yml up -d
```

## ESP32-C3 固件

打开：

```text
esp32c3_epaper_frame/esp32c3_epaper_frame.ino
```

默认读取：

```cpp
static const char* WIFI_SSID = "YOUR_WIFI_SSID";
static const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
static const char* FRAME_URL = "http://YOUR_NAS_IP:15002/frame.bin";
static const char* CONFIG_URL = "http://YOUR_NAS_IP:15002/api/device/config";
```

本地烧录前，把占位符改成你的 WiFi 和 NAS 地址。例如：

```cpp
static const char* WIFI_SSID = "你的WiFi";
static const char* WIFI_PASSWORD = "你的WiFi密码";
static const char* FRAME_URL = "http://YOUR_NAS_IP:15002/frame.bin";
static const char* CONFIG_URL = "http://YOUR_NAS_IP:15002/api/device/config";
```

## frame.bin 格式

```text
0..3      magic: EPD1
4         version: 1
5..6      width: 400, little-endian
7..8      height: 300, little-endian
9         planes: 2
10        flags: 0
11..14    plane bytes: 15000, little-endian
15..15014 black plane
15015..30014 red plane
```

每个 plane 都是 MSB first，一位代表一个像素。

## 当前布局

- 左上角报警圆：
  - 正常：白色空心圆
  - 风险：圆形内部红色棋盘
  - 危险：红色空心圆
- 顶部：名称、温度、负载、日期、时间
- 状态行：CPU、MEM、DISK，其中 MEM/DISK 使用 `已用/总量G(T)` 格式
- 中部：24 小时 CPU、内存、路由器 CPU 折线图
- 下部：Docker 容器表格，按 CPU 占用从高到低排序

默认报警阈值：

- CPU：70% 风险，90% 危险
- 内存：80% 风险，90% 危险
- 1 分钟负载按核心数折算：0.8/core 风险，1.2/core 危险
