# Auto Studio — README

## 1. Tổng quan
Auto Studio là ứng dụng web tạo nội dung và video prompt bằng AI, phục vụ luồng sản xuất content cho nhiều thể loại kênh YouTube.

Kiến trúc:
- Backend: `Flask` (`server.py`).
- Frontend: `HTML + CSS + Vanilla JS` (`web/index.html`, `web/style.css`, `web/app.js`).
- Giao tiếp thời gian thực: `SSE` tại `/api/events`.

---

## 2. Chức năng chính
- Viết Content theo chủ đề + style.
- Tách Segment từ nội dung.
- Tạo Video Prompt theo từng Segment.
- Tạo tiếp các Video Prompt còn thiếu.
- Quản lý Queue cho nhiều project.
- Quản lý danh sách project đã lưu.
- Quản lý Prompt Style (content/video).
- Gửi/Nhận file P2P bằng token 6 ký tự.
- Dịch nhanh VI theo chế độ xem tạm (`Dịch VI` / `Ngôn Ngữ Gốc`).
- Trang Hướng dẫn ngay trong ứng dụng (đọc từ `guild.md`).

---

## 3. Cấu trúc file

### 3.1 Phân loại: APP vs USER

```
AutoStudio/
│
│  ── APP CODE (an toàn để cập nhật/thay thế) ──────────────
├── AutoStudio.vbs          Launcher: tìm Chrome, mở Guest mode, chạy server
├── start_web.bat            Launcher phụ: chạy server + mở trình duyệt mặc định
├── update.bat               Script cập nhật 1 click (git pull)
├── server.py                API backend, SSE, pipeline, queue, translate, P2P
├── requirements.txt         Dependencies (flask, requests)
├── readme.md                Tài liệu kỹ thuật
├── guild.md                 Hướng dẫn sử dụng (hiển thị trong app)
├── .gitignore               Bảo vệ data user khi cập nhật
│
├── core/                    Module xử lý AI
│   ├── content_writer.py    Sinh nội dung content
│   ├── ai_splitter.py       Tách segment bằng AI + lấy models
│   ├── splitter.py          Tách segment cục bộ/fallback
│   ├── video_prompter.py    Sinh video prompt theo segment
│   └── project_manager.py   Tạo/lưu/load/list/xóa project
│
├── web/                     Frontend
│   ├── index.html           Layout + menu + modal + các page
│   ├── style.css            Giao diện CSS
│   ├── app.js               State/UI logic, render, API calls
│   └── *.png, *.ico         Logo, favicon, icons
│
├── scripts/                 Scripts hỗ trợ
│   ├── run_server.bat       Bootstrap embedded Python + chạy server
│   └── _win_dialog.py       Helper dialog chọn file/folder (Win32 COM)
│
│  ── USER DATA (không bao giờ bị ghi đè khi cập nhật) ────
├── data/
│   ├── config.json          Cấu hình endpoint/model/output (user)
│   ├── styles.json          Content styles (user custom)
│   ├── video_styles.json    Video styles (user custom)
│   ├── default_styles.json  Content styles mặc định (app ships)
│   ├── default_video_styles.json  Video styles mặc định (app ships)
│   ├── queue_state.json     Trạng thái queue
│   └── p2p_shares.json      Metadata token P2P
│
├── output/                  Dữ liệu project (project.json, script.txt, ...)
│
└── runtime/                 Runtime không đưa vào Git
    ├── python/              Embedded Python + pip packages
    ├── chrome-guest-session/ Session Chrome tạm
    └── server_boot.log      Log khởi động
```

### 3.2 Nguyên tắc cập nhật

| Thư mục/file | Loại | Khi cập nhật |
|---|---|---|
| `server.py`, `core/`, `web/`, `scripts/` | APP | Thay thế hoàn toàn ✅ |
| `AutoStudio.vbs`, `*.bat`, `*.md` | APP | Thay thế hoàn toàn ✅ |
| `data/default_*.json` | APP | Thay thế → ship styles mới ✅ |
| `data/config.json` | USER | **Không chạm** ❌ |
| `data/styles.json`, `data/video_styles.json` | USER | **Không chạm** ❌ |
| `data/queue_state.json`, `data/p2p_shares.json` | USER | **Không chạm** ❌ |
| `output/`, `runtime/` | USER | **Không chạm** ❌ |

---

## 4. Luồng khởi động

### 4.1 AutoStudio.vbs (launcher chính)
```
1. Tìm Chrome (registry → file paths thông dụng)
2. Kill server cũ trên port 5000
3. Chạy scripts\run_server.bat (ẩn)
4. Poll http://localhost:5000/api/config mỗi 300ms (tối đa 300s)
5. Mở Chrome Guest mode: --app=http://localhost:5000
6. Đợi Chrome đóng → dọn session + kill server
```

### 4.2 scripts\run_server.bat
```
1. Kiểm tra runtime\python\python.exe tồn tại
2. Đảm bảo sys.path trong _pth file
3. Kiểm tra deps (marker file .deps_ok → skip nếu đã OK)
4. Chạy python server.py
```

### 4.3 server.py khởi động
```
1. _init_data_dir():
   - Tạo data/ nếu chưa có
   - Migrate config.json từ root → data/ (nếu có file cũ)
   - Copy default_styles → styles nếu user chưa có
2. Load config, start Flask on port 5000
```

---

## 5. Luồng hoạt động

### 5.1 Full pipeline
`POST /api/pipeline/start`
1. `write`: sinh nội dung content (stream + log).
2. `split`: tách segment.
3. `video`: tạo video prompt.

### 5.2 Chạy từng bước
`POST /api/pipeline/step` với `step`:
- `write`
- `split`
- `video`
- `continue_prompts`

### 5.3 Cơ chế chỉnh sửa và lưu thủ công
#### Content
- Chỉnh sửa ở tab Content không tự lưu.
- Chỉ lưu khi bấm `Cập Nhật Content` và xác nhận.
- Nếu rời tab/chức năng khi có thay đổi chưa lưu, app yêu cầu xác nhận.

#### Segment / Video Prompt popup
- Đóng popup không tự lưu.
- Chỉ lưu khi bấm `Cập Nhật Segments` hoặc `Cập Nhật Video Prompt`.
- Bản dịch VI trong popup chỉ để đọc, không ghi vào dữ liệu gốc.

---

## 6. Cấu hình (`data/config.json`)
Các khóa thường dùng:
- `endpoint`: URL API OpenAI-compatible.
- `api_key`: API key cho endpoint.
- `model`: model mặc định cho bước viết content.
- `model_video`: model mặc định cho bước video prompt.
- `model_translate`: model dịch (nếu dùng).
- `output_dir`: thư mục lưu project.
- `p2p_download_dir`: thư mục nhận file P2P trên máy local.
- `wpm`: tốc độ đọc trung bình để ước lượng duration segment.
- `target_seconds`: thời lượng mục tiêu mỗi segment.

Lưu ý:
- App luôn trả JSON UTF-8.
- Config tự migrate từ root vào `data/` khi khởi động lần đầu sau cập nhật.

---

## 7. Cập nhật ứng dụng

### 7.1 Setup lần đầu (trên thiết bị mới)
```bash
git clone <repo_url> AutoStudio
cd AutoStudio
# Copy runtime/python/ từ thiết bị gốc (hoặc cài embedded Python)
# Chạy AutoStudio.vbs
```

### 7.2 Cập nhật (trên thiết bị đã có)
```bash
# Cách 1: Chạy update.bat (1 click)
update.bat

# Cách 2: Thủ công
git pull origin main
```

### 7.3 Những gì KHÔNG bị ảnh hưởng khi cập nhật
- API keys, endpoint, model settings (`data/config.json`)
- Custom styles (`data/styles.json`, `data/video_styles.json`)
- Project đã tạo (`output/`)
- Queue state, P2P shares (`data/queue_state.json`, `data/p2p_shares.json`)
- Embedded Python runtime (`runtime/`)

---

## 8. API chính

### 8.1 Config / models
- `GET /api/config`
- `POST /api/config`
- `GET /api/models`

### 8.2 Pipeline
- `POST /api/pipeline/start`
- `POST /api/pipeline/pause`
- `POST /api/pipeline/stop`
- `GET /api/pipeline/state`
- `POST /api/pipeline/step`
- `POST /api/pipeline/regenerate-prompt`

### 8.3 Projects
- `GET /api/projects`
- `GET /api/projects/<id>`
- `POST /api/projects/<id>/update` (lưu chỉnh sửa thủ công)
- `DELETE /api/projects/<id>`
- `GET /api/projects/<id>/export`
- `POST /api/projects/<id>/open-folder`

### 8.4 Queue
- `GET /api/queue`
- `POST /api/queue`
- `PUT /api/queue/<index>`
- `DELETE /api/queue/<index>`
- `POST /api/queue/clear`
- `POST /api/queue/start`

### 8.5 P2P
- `GET /api/p2p-download-dir`
- `POST /api/p2p-download-dir`
- `POST /api/p2p-download-dir/pick`
- `GET /api/p2p/shares`
- `POST /api/p2p/shares`
- `GET /api/p2p/shares/<token>`
- `PUT /api/p2p/shares/<token>`
- `DELETE /api/p2p/shares/<token>`
- `POST /api/p2p/shares/<token>/files/add`
- `POST /api/p2p/shares/<token>/files/remove`
- `GET /api/p2p/shares/<token>/info`
- `POST /api/p2p/shares/<token>/open-download-folder`
- `POST /api/p2p/download/<token>`

### 8.6 Khác
- `POST /api/split/manual`
- `POST /api/split/ai`
- `POST /api/translate/vi`
- `GET /api/guide` (đọc `guild.md`)
- `GET /api/events` (SSE log/state/script chunk)

---

## 9. Xử lý sự cố

### 9.1 Không tải được model
- Kiểm tra endpoint, API key trong `Cấu Hình`.
- Bấm `Lưu & Kiểm Tra`, xem log.

### 9.2 Lỗi font/encoding tiếng Việt
- Đảm bảo file và API trả UTF-8.
- App đã bật xử lý UTF-8 cho JSON/log ở cả frontend và backend.

### 9.3 Project có segment/prompt không khớp
- Cập nhật Content trước.
- Tạo lại prompt thiếu bằng `Tạo Video Prompt Thiếu`.

### 9.4 P2P không thấy file sau khi tải
- Kiểm tra `Thư Mục Lưu File P2P` trong Cấu Hình.
- Tải lại token để app tạo lại folder.
- Dùng `Token Đã Download` để mở thư mục nhận file.

### 9.5 Dialog chọn file/folder
- App dùng `IFileOpenDialog` COM (Win32 API) qua helper script `scripts/_win_dialog.py`.
- Dialog mở giao diện Explorer hiện đại, nổi lên foreground.
- Nếu dialog không hiện: kiểm tra taskbar, có thể bị ẩn sau cửa sổ khác.

---

## 10. Hướng dẫn người dùng
Tài liệu sử dụng chi tiết:
- File `guild.md`
- Trong app: menu `Hướng Dẫn`
