# Auto Studio — README

## 1. Tổng quan
Auto Studio là ứng dụng web tạo nội dung và video prompt bằng AI, phục vụ luồng sản xuất content cho nhiều thể loại kênh YouTube.

Kiến trúc:
- Backend: `Flask` (`server.py`).
- Frontend: `HTML + CSS + Vanilla JS` (`web/index.html`, `web/style.css`, `web/app.js`).
- Giao tiếp thời gian thực: `SSE` tại `/api/events`.
- Truyền file P2P: `WebRTC` qua PeerJS (DataChannel, binary protocol).

---

## 2. Chức năng chính
- Viết Content theo chủ đề + style.
- Tách Segment từ nội dung.
- Tạo Video Prompt theo từng Segment.
- Tạo tiếp các Video Prompt còn thiếu.
- Quản lý Queue cho nhiều project.
- Quản lý danh sách project đã lưu.
- Quản lý Prompt Style (content/video).
- **Gửi/Nhận file P2P qua WebRTC** — truyền trực tiếp giữa 2 máy qua Internet bằng Peer ID.
- **Cập nhật tự động** — kiểm tra GitHub, tải bản mới, giữ nguyên cấu hình người dùng.
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
├── server.py                API backend, SSE, pipeline, queue, translate, P2P, auto-update
├── requirements.txt         Dependencies (flask, requests)
├── VERSION                  Số phiên bản hiện tại (semver)
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
│   ├── app.js               State/UI logic, render, API calls, WebRTC
│   └── *.png, *.ico         Logo, favicon, icons
│
├── scripts/                 Scripts hỗ trợ
│   ├── run_server.bat       Bootstrap embedded Python + chạy server
│   └── _win_dialog.py       Helper dialog chọn file/folder (Win32 COM)
│
│  ── USER DATA (không bao giờ bị ghi đè khi cập nhật) ────
├── data/
│   ├── config.json          Cấu hình endpoint/model/output/github_token (user)
│   ├── styles.json          Content styles (user custom)
│   ├── video_styles.json    Video styles (user custom)
│   ├── default_styles.json  Content styles mặc định (app ships)
│   ├── default_video_styles.json  Video styles mặc định (app ships)
│   ├── queue_state.json     Trạng thái queue
│   └── p2p_shares.json      Metadata share P2P
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
3. Background: kiểm tra cập nhật GitHub API
4. Background: probe models sẵn sàng
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

## 6. P2P File Transfer (WebRTC)

### 6.1 Tổng quan
Truyền file trực tiếp giữa 2 máy qua Internet bằng WebRTC DataChannel + PeerJS signaling.

### 6.2 Flow
```
Sender:
1. Chọn file/folder → bấm "Chia sẻ"
2. Tự động tạo token nội bộ + PeerJS peer
3. Hiện Peer ID (VD: AS-X7K9M2)
4. Gửi Peer ID cho người nhận

Receiver:
1. Nhập Peer ID → bấm "Kết nối"
2. Nhận metadata (danh sách file, kích thước)
3. Tải từng file qua DataChannel (raw binary, 256KB chunks)
4. Buffer 1MB trước khi ghi xuống đĩa
5. Hiển thị progress, tốc độ, phần trăm
```

### 6.3 Điều khiển
- **Tạm dừng / Tiếp tục**: dừng nhận chunk data
- **Huỷ**: đóng kết nối, huỷ toàn bộ

### 6.4 Backend endpoints
- `GET /api/p2p/share-meta/<token>` — metadata file list cho receiver
- `GET /api/p2p/stream-file` — stream file content cho sender browser gửi qua DataChannel
- `POST /api/p2p/save-chunk` — receiver ghi chunk xuống đĩa
- `POST /api/p2p/save-done` — hoàn tất file transfer, di chuyển vào thư mục lưu

---

## 7. Cập nhật tự động

### 7.1 Kiểm tra phiên bản
- App tự so sánh `VERSION` local với GitHub release mới nhất (GitHub API).
- Hỗ trợ repo private (đặt `github_token` trong `data/config.json`).
- Banner hiện trên giao diện khi có bản mới.

### 7.2 Quy tắc phiên bản (Semver)
| Loại | Ví dụ | Khi nào |
|---|---|---|
| **Patch** (x.y.**Z**) | v1.4.0 → v1.4.**1** | Chỉ fix bug |
| **Minor** (x.**Y**.0) | v1.4.1 → v1.**5**.0 | Bổ sung tính năng mới |
| **Major** (**X**.0.0) | v1.x → v**2**.0.0 | Thay đổi lớn, do quản trị viên quyết định |

### 7.3 Flow cập nhật tự động
```
1. Banner hiện: "Có bản cập nhật mới v1.4.0 → v1.4.1"
2. User bấm "Cập nhật"
3. Server tải ZIP từ GitHub release
4. Giải nén vào _update_staging/
5. Copy cấu hình user (config.json, styles.json, video_styles.json, p2p_shares.json) vào bản mới
6. Tạo _do_update.bat
7. Server thoát → batch script copy file mới → khởi động lại AutoStudio.vbs
8. Trang web tự reconnect và reload
```

### 7.4 Config giữ nguyên khi cập nhật
- `data/config.json` — API keys, endpoint, settings
- `data/styles.json` — User custom styles
- `data/video_styles.json` — Video style presets
- `data/p2p_shares.json` — P2P share metadata
- `output/` — Tất cả project đã tạo
- `runtime/` — Embedded Python

---

## 8. Cấu hình (`data/config.json`)
Các khóa thường dùng:
- `endpoint`: URL API OpenAI-compatible.
- `api_key`: API key cho endpoint.
- `model`: model mặc định cho bước viết content.
- `model_video`: model mặc định cho bước video prompt.
- `model_translate`: model dịch (nếu dùng).
- `output_dir`: thư mục lưu project.
- `p2p_download_dir`: thư mục nhận file P2P trên máy local.
- `github_token`: token GitHub (cho repo private, dùng khi cập nhật).
- `wpm`: tốc độ đọc trung bình để ước lượng duration segment.
- `target_seconds`: thời lượng mục tiêu mỗi segment.

---

## 9. API chính

### 9.1 Config / Models
- `GET /api/config`
- `POST /api/config`
- `GET /api/models`
- `GET /api/version`

### 9.2 Pipeline
- `POST /api/pipeline/start`
- `POST /api/pipeline/pause`
- `POST /api/pipeline/stop`
- `GET /api/pipeline/state`
- `POST /api/pipeline/step`
- `POST /api/pipeline/regenerate-prompt`

### 9.3 Projects
- `GET /api/projects`
- `GET /api/projects/<id>`
- `POST /api/projects/<id>/update`
- `DELETE /api/projects/<id>`
- `GET /api/projects/<id>/export`
- `POST /api/projects/<id>/open-folder`

### 9.4 Queue
- `GET /api/queue`
- `POST /api/queue`
- `PUT /api/queue/<index>`
- `DELETE /api/queue/<index>`
- `POST /api/queue/clear`
- `POST /api/queue/start`

### 9.5 P2P
- `GET /api/p2p-download-dir`
- `POST /api/p2p-download-dir`
- `POST /api/p2p-download-dir/pick`
- `GET /api/p2p/shares` / `POST /api/p2p/shares`
- `GET /api/p2p/shares/<token>` / `PUT` / `DELETE`
- `POST /api/p2p/shares/<token>/files/add`
- `POST /api/p2p/shares/<token>/files/remove`
- `GET /api/p2p/share-meta/<token>`
- `GET /api/p2p/stream-file`
- `POST /api/p2p/save-chunk`
- `POST /api/p2p/save-done`

### 9.6 Utilities
- `POST /api/split/manual` / `POST /api/split/ai`
- `POST /api/translate/vi`
- `POST /api/open-folder`
- `GET /api/guide`
- `GET /api/events` (SSE log/state/script chunk)

### 9.7 Auto-Update
- `GET /api/check-update`
- `POST /api/apply-update`

---

## 10. Xử lý sự cố

### 10.1 Không tải được model
- Kiểm tra endpoint, API key trong `Cấu Hình`.
- Bấm `Lưu & Kiểm Tra`, xem log.

### 10.2 Lỗi font/encoding tiếng Việt
- Đảm bảo file và API trả UTF-8.
- App đã bật xử lý UTF-8 cho JSON/log ở cả frontend và backend.

### 10.3 Project có segment/prompt không khớp
- Cập nhật Content trước.
- Tạo lại prompt thiếu bằng `Tạo Video Prompt Thiếu`.

### 10.4 P2P WebRTC
- **peer-unavailable**: Người gửi chưa bấm "Chia sẻ" hoặc Peer ID sai.
- **File hỏng**: Kiểm tra kết nối mạng, thử lại.
- **Tốc độ chậm**: Phụ thuộc vào băng thông upload của sender và khoảng cách mạng.

### 10.5 Cập nhật thất bại
- Kiểm tra kết nối mạng.
- Nếu repo private: đặt `github_token` trong `data/config.json`.
- Nếu lỗi file lock: đóng tất cả ứng dụng đang dùng file trong thư mục AutoStudio.

### 10.6 Dialog chọn file/folder
- App dùng `IFileOpenDialog` COM (Win32 API) qua helper script `scripts/_win_dialog.py`.
- Dialog mở giao diện Explorer hiện đại, nổi lên foreground.
- Nếu dialog không hiện: kiểm tra taskbar, có thể bị ẩn sau cửa sổ khác.

---

## 11. Hướng dẫn người dùng
Tài liệu sử dụng chi tiết:
- File `guild.md`
- Trong app: menu `Hướng Dẫn`
