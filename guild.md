# Guild — Hướng Dẫn Sử Dụng Auto Studio

## 1. Mục tiêu ứng dụng
Auto Studio giúp bạn tạo nội dung kênh YouTube theo pipeline:
1. Viết Content.
2. Tách Segment theo dòng (mỗi dòng là 1 segment đọc).
3. Tạo Video Prompt theo từng Segment.

Ứng dụng hỗ trợ chạy full pipeline, chạy từng bước, hàng chờ nhiều project, quản lý project đã lưu và quản lý style prompt.

---

## 2. Khởi động ứng dụng
### Cách 1: dùng script
- Chạy `AutoStudio.vbs` để mở app nhanh trên Windows.

### Cách 2: chạy thủ công
1. Cài dependencies:
```bash
pip install -r requirements.txt
```
2. Chạy backend:
```bash
python server.py
```
3. Mở trình duyệt tại:
```text
http://127.0.0.1:5000
```

---

## 3. Cấu hình ban đầu (bắt buộc)
Vào menu `Cấu Hình`:
1. Nhập `Endpoint` API.
2. Nhập `API Key`.
3. Bấm `Lưu & Kiểm Tra` để lấy danh sách model.
4. Chọn `Thư mục Output` để lưu project.
5. Chọn `Thư Mục Lưu File P2P` để lưu file nhận qua token.
Nếu chưa cấu hình đúng endpoint/key, các bước gọi AI sẽ không chạy được.
Nếu chưa chọn thư mục P2P riêng, app sẽ dùng thư mục mặc định `P2P_Downloads` bên trong `Thư mục Output`.
Launcher sẽ dùng Google Chrome đã cài trên máy ở chế độ `Guest` (không dùng profile cố định). Khi bạn đóng cửa sổ trình duyệt AutoStudio, server sẽ tự dừng để app không chạy nền.

---

## 4. Màn hình Tạo Content (Writer)
## 4.1 Cột trái
- Chủ đề / Nội dung / Yêu cầu.
- Ngôn ngữ.
- Content Style.
- Model viết Content.
- Video Prompt Style.
- Model Video Prompt.

### Nút chạy chính
- `Bắt Đầu`: chạy full pipeline.
- `Tạm Dừng`: pause/resume.
- `Huỷ`: chỉ dùng khi pipeline đang pause.

### Chạy từng bước
- `Viết Content`
- `Tách Đoạn`
- `Tạo Video Prompt`
- `Tạo Video Prompt Thiếu` (chỉ hiện khi còn thiếu prompt)

## 4.2 Tab Content
- Có cột số dòng bên trái.
- Có các nút:
  - `Dịch VI` / `Ngôn Ngữ Gốc`
  - `Cập Nhật Content`
  - `Huỷ chỉnh sửa`

### Quy tắc chỉnh sửa Content
- Chỉnh sửa trong tab Content **không tự lưu**.
- Chỉ khi bấm `Cập Nhật Content` và xác nhận thì mới lưu vào project.
- Nếu rời tab/chuyển chức năng khi đang có thay đổi chưa lưu, app sẽ hỏi:
  - `Xác nhận lưu`: lưu nội dung.
  - `Huỷ chỉnh sửa`: bỏ thay đổi và quay về bản gốc.

### Quy tắc dịch Content
- `Dịch VI` chỉ tạo bản xem tạm.
- Khi đang xem bản dịch, nút đổi thành `Ngôn Ngữ Gốc` để quay lại bản gốc.
- Bản dịch không ghi đè dữ liệu gốc nếu bạn chưa xác nhận cập nhật.

## 4.3 Tab Video Prompt
- Bảng gồm Segment và Video Prompt.
- Text trong mỗi ô hiển thị tối đa 4 dòng, dài hơn sẽ có dấu `...`.
- Click vào Segment/Video Prompt để mở popup chi tiết.

### Popup Segment / Video Prompt
- Tiêu đề theo ID dòng.
- Có nút `Dịch VI` / `Ngôn Ngữ Gốc`.
- Có nút `Cập Nhật Segments` hoặc `Cập Nhật Video Prompt`.

### Quy tắc popup
- Đóng popup thì **không tự lưu**.
- Chỉ lưu khi bấm nút `Cập Nhật...` trong popup.
- Bản dịch VI trong popup là chế độ đọc tạm, không tự lưu vào project.

---

## 5. Menu Hàng Chờ
Dùng khi muốn chạy nhiều chủ đề liên tiếp:
1. Nhập chủ đề + cấu hình model/style/ngôn ngữ.
2. Bấm `Thêm Vào Hàng Chờ`.
3. Bấm `Chạy` để xử lý tuần tự.

Có thể sửa/xóa từng item trước khi chạy.

---

## 6. Menu Projects
Hiển thị project đã lưu với thông tin:
- Tên folder, chủ đề rút gọn.
- Thời gian, số segment, số prompt, ngôn ngữ, style.
- Trạng thái.

Các thao tác:
- Mở lại project.
- Mở thư mục project.
- Xóa project.

---

## 7. Menu Gửi File P2P
Màn hình này có 2 phần:

### 7.1 Cột trái
- `Nhận File Theo Token`: nhập token 6 chữ cái rồi bấm `Tải về`.
- File sẽ được lưu trực tiếp vào thư mục P2P đã cấu hình.
- App tự tạo folder theo `Tên Nhóm File` của token, không tải ZIP.

### 7.2 Tạo token gửi file
- Nhập `Tên Nhóm File`.
- Bấm `Chọn 1/N File` hoặc `Chọn Folder`.
- Có thể kéo thả file vào vùng chọn nhanh.
- Bấm `Tạo Token` để sinh token chia sẻ.

### 7.3 Cột phải
- `Danh Sách File P2P Đã Tạo`: dùng để copy token, chỉnh sửa hoặc xóa token.
- `Danh Sách File P2P Đã Download`: hiển thị các token đã tải trên máy này.
- Mỗi token đã download có nút `Mở thư mục` để mở đúng nơi file đã lưu.

---

## 8. Menu Quản Lý Prompt
Quản lý:
- Content Styles.
- Video Styles.

Bạn có thể thêm/sửa/xóa style.
Popup style có nút dịch prompt sang tiếng Việt theo chế độ xem tạm (`Dịch VI` / `Ngôn Ngữ Gốc`).

---

## 9. Cơ chế dịch
- App dùng model dịch chuyên biệt (ẩn khỏi các dropdown model xử lý chính).
- Dịch chỉ dùng cho hiển thị và tham chiếu nhanh.
- Khi bật bản dịch, trường nhập sẽ chuyển sang read-only.

---

## 10. Log và debug
Panel Log hiển thị:
- Bước đang chạy.
- API request chính.
- Trạng thái pipeline.
- Thông tin segment/prompt cần xử lý.

Khi cần debug, ưu tiên kiểm tra theo thứ tự:
1. Endpoint/API key.
2. Model khả dụng.
3. Dữ liệu input (chủ đề, style, segment).
4. Log lỗi cụ thể ở panel phải.

---

## 11. Thư mục dữ liệu project
Mỗi project lưu trong thư mục output đã cấu hình, gồm:
- `project.json`: dữ liệu đầy đủ.
- `script.txt`: content.
- `video_prompts.txt`: danh sách video prompt.

File nhận qua P2P không lưu trong thư mục project.
Chúng được lưu trong `Thư Mục Lưu File P2P`, theo cấu trúc:
- `<thư-mục-p2p>/<tên-nhóm-file>/...`

---

## 12. Các lưu ý quan trọng
- Luôn cấu hình endpoint/key trước khi chạy.
- Luôn bấm `Cập Nhật...` nếu muốn lưu chỉnh sửa.
- Không đóng app giữa lúc pipeline đang chạy nếu chưa cần thiết.
- Nếu cần tiếp tục prompt thiếu, mở project và bấm `Tạo Video Prompt Thiếu`.
- Nếu dùng P2P, kiểm tra đúng `Thư Mục Lưu File P2P` trước khi tải token.

---

## 13. Sự cố thường gặp
### 1) Không thấy model
- Kiểm tra endpoint.
- Kiểm tra API key.
- Bấm lại `Lưu & Kiểm Tra`.

### 2) Bấm chạy nhưng không tạo dữ liệu
- Xem log có báo lỗi API không.
- Kiểm tra style/model đã chọn còn tồn tại không.

### 3) Dịch chậm
- Dịch phụ thuộc model và tải hệ thống API.
- Dùng model dịch nhanh đã cấu hình sẵn trong app.

### 4) Mất chỉnh sửa
- Chỉnh sửa chỉ được lưu khi bạn bấm `Cập Nhật...` và xác nhận.
- Nếu chọn `Huỷ chỉnh sửa` thì app sẽ phục hồi bản gốc.

### 5) Tải P2P xong nhưng không thấy file
- Mở `Cấu Hình` để kiểm tra `Thư Mục Lưu File P2P`.
- Vào cột `Danh Sách File P2P Đã Download` và bấm `Mở thư mục`.
- Nếu thư mục cũ đã bị xóa, tải lại token để app tạo lại folder mới.
