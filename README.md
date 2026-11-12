# FinSight AI — Hệ thống Trợ lý Phân tích Báo cáo Tài chính Đa phương thức (Agentic RAG & Text-to-SQL)

> **Đồ án Công nghệ Thông tin**  
> **Đề tài:** Xây dựng hệ thống trợ lý phân tích báo cáo tài chính doanh nghiệp đa phương thức kết hợp kiến trúc Agentic RAG và truy vấn dữ liệu có cấu trúc (Text-to-SQL).

---

## 1. Giới thiệu tổng quan

**FinSight AI** giải quyết bài toán tra cứu, đối chiếu và phân tích báo cáo tài chính (Báo cáo thường niên, Báo cáo tài chính hợp nhất quý/năm, SEC 10-K) vốn chứa nhiều bảng số liệu phức tạp, biểu đồ và thuyết minh dài.

### Điểm nổi bật của hệ thống
1. **Multimodal Data Ingestion & ETL:** Bóc tách văn bản, bảng biểu phức tạp (giữ nguyên cấu trúc Markdown/JSON) và biểu đồ từ PDF; đồng thời chuẩn hóa các chỉ số tài chính cốt lõi vào cơ sở dữ liệu quan hệ (PostgreSQL).
2. **Kiến trúc Hybrid (Agentic RAG + Text-to-SQL):**
   - **Text-to-SQL & Python Calculator Tool:** Xử lý các câu hỏi định lượng, tính toán tỷ số tài chính (ROE, ROA, Biên lợi nhuận, Tăng trưởng YoY) chính xác 100%, tránh lỗi tính nhẩm của LLM.
   - **Hybrid Search (Vector + BM25) + Cross-Encoder Reranker:** Truy xuất chính xác phần thuyết minh, chiến lược kinh doanh, yếu tố rủi ro từ kho tài liệu.
3. **Self-Correcting Agent (LangGraph):** Tự động phân loại câu hỏi, kiểm tra độ liên quan của tài liệu tìm được (Relevance Grader), viết lại câu truy vấn nếu thiếu ngữ cảnh và chống bịa đặt thông tin (Hallucination Check).
4. **Trích dẫn nguồn trực quan (Page-level Citation):** Mọi con số và nhận định đều gắn kèm trích dẫn tới đúng tên tài liệu, số trang và bảng biểu gốc trên giao diện.
5. **Đánh giá thực nghiệm định lượng (RAGAS & Benchmark):** Tích hợp sẵn pipeline chấm điểm tự động (Faithfulness, Answer Relevancy, Context Precision/Recall, Execution Accuracy) phục vụ báo cáo khoa học.

---

## 2. Bản đồ tài liệu dự án (Single Source of Truth)

Để dự án dễ mở rộng và không bị rối khi phát triển cùng AI (**Spec-Driven Vibe Coding**), toàn bộ thiết kế được tách thành các tài liệu chuẩn dưới đây:

| Tài liệu | Mục đích | Khi nào cần đọc/cập nhật? |
| :--- | :--- | :--- |
| [AGENTS.md](file:///d:/Đồ án CNTT/AGENTS.md) | Quy tắc lập trình, tiêu chuẩn kiến trúc, quy ước đặt tên và kỷ luật làm việc cho AI Agent. | Luôn yêu cầu AI đọc đầu tiên trong mỗi phiên làm việc. |
| [docs/SPEC.md](file:///d:/Đồ án CNTT/docs/SPEC.md) | Đặc tả nghiệp vụ, yêu cầu chức năng, phi chức năng, kịch bản sử dụng và định hướng mở rộng. | Khi thêm tính năng mới hoặc kiểm tra yêu cầu nghiệp vụ. |
| [docs/ARCHITECTURE.md](file:///d:/Đồ án CNTT/docs/ARCHITECTURE.md) | Kiến trúc hệ thống, cấu trúc thư mục, thiết kế Database (PostgreSQL & Qdrant), luồng dữ liệu và API. | Khi thiết kế module mới, sửa schema DB hoặc tích hợp thư viện. |
| [docs/TASKS.md](file:///d:/Đồ án CNTT/docs/TASKS.md) | Lộ trình triển khai chia theo từng Milestone nhỏ kèm **Prompt mẫu** và tiêu chí nghiệm thu. | Dùng hằng ngày để theo dõi tiến độ và giao việc cho AI. |

---

## 3. Quy trình "Vibe Code" chuẩn cho dự án này

1. **Mở phiên chat mới cho mỗi Task nhỏ:** Tránh làm một mạch quá nhiều tính năng trong một cuộc hội thoại dài.
2. **Neo ngữ cảnh đầu phiên:** Bắt đầu bằng câu lệnh:
   ```text
   Hãy đọc AGENTS.md, docs/ARCHITECTURE.md và docs/TASKS.md. Hôm nay chúng ta sẽ thực hiện [Mã Task trong TASKS.md].
   ```
3. **Kiểm thử trước khi chuyển bước:** Mỗi task bắt buộc phải chạy qua `pytest` hoặc script kiểm thử thực tế.
4. **Commit Git ngay khi chạy ổn:** Lưu lại trạng thái code sạch sau mỗi task hoàn thành (`git commit -m "feat: complete task X.Y"`).
5. **Cập nhật tài liệu:** Đánh dấu `[x]` vào `docs/TASKS.md` và cập nhật `docs/ARCHITECTURE.md` nếu có thay đổi thiết kế.
