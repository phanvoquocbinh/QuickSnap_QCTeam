# QuickSnap – chụp màn hình & quay video chỉ bằng phím PrtSc

## Cách dùng
Nhấn **PrtSc** ở bất cứ đâu → hiện menu **📷 Chụp ảnh** / **🎥 Quay video** (Esc để đóng).

### 📷 Chụp ảnh
1. Kéo chuột khoanh vùng (hoặc click vào 1 cửa sổ, hoặc Enter = cả màn hình). Khoanh sai thì kéo lại.
2. Hiện 3 nút:
   - **✔ Xác nhận** → mở cửa sổ chỉnh sửa: chọn Khoanh tròn / Chữ nhật / Mũi tên / Tô sáng… rồi khoanh chỗ cần highlight.
     Ảnh **tự động nằm trong clipboard** (cả khi vừa vẽ thêm) → sang Zalo, Teams, Word, Excel… nhấn **Ctrl+V** để dán.
     Bấm **✔ Xong** để copy & đóng cửa sổ.
   - **⇕ Cuộn chuột** → cuộn chuột xuống từ từ, ảnh dài ra theo. Bấm **✔ OK** (hoặc PrtSc) để sang bước chỉnh sửa như trên,
     **✖ Tắt** để huỷ. Có nút **Tự cuộn** nếu muốn app tự cuộn đến cuối trang.
   - **✖ Tắt** (hoặc Esc) → huỷ.

### 🎥 Quay video
Khoanh vùng (hoặc Enter = cả màn hình) → **● Bắt đầu quay**. Nút **■ DỪNG & LƯU** nằm ở **góc trên bên phải** màn hình
(kéo chỗ ⠿ để di chuyển; nhấn PrtSc cũng dừng). Video .mp4 lưu trong `Videos\QuickSnap`.

Viền đỏ và các nút điều khiển **không** bị dính vào ảnh/video (Windows 10 bản 2004 trở lên).

## Chạy bằng Visual Studio Code
1. **File > Open Folder…** → chọn thư mục `QuickSnap`, cài extension **Python** nếu được gợi ý.
2. Mở Terminal (**Ctrl+`**), chạy lần đầu:
   ```
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   ```
   (Nếu báo "running scripts is disabled": chạy `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` trước.)
3. Nhấn **F5** để chạy.

Cách khác: double-click `run.bat`. Muốn ra file `.exe`: double-click `build.bat` → `dist\QuickSnap.exe`.

## Chạy nền
- Đóng cửa sổ chính → app vẫn chạy dưới **khay hệ thống** (góc phải taskbar). Chuột phải icon để Thoát.
- Tick **Khởi động cùng Windows** để lúc nào bật máy cũng nhấn PrtSc được.

## Nếu nhấn PrtSc lại mở Snipping Tool của Windows
Vào **Settings > Accessibility > Keyboard** → tắt **"Use the Print screen key to open screen capture"**, rồi mở lại QuickSnap.
