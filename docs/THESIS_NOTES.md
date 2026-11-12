# Ghi chú Lý thuyết, Thiết kế & Thực nghiệm (Thesis Notes) — FinSight AI

> File này dùng để ghi lại **lý do lựa chọn công nghệ (Design Decisions)**, **cơ sở lý thuyết** và **bảng số liệu thực nghiệm** trong quá trình làm dự án. Khi viết cuốn báo cáo đồ án tốt nghiệp hoặc ôn tập phỏng vấn, bạn chỉ cần mở file này ra là có sẵn toàn bộ luận điểm bảo vệ.

---

## 1. Cấu trúc Đề cương Báo cáo Đồ án Gợi ý (5 Chương chuẩn)

- **Chương 1: Tổng quan về đề tài**
  - 1.1. Đặt vấn đề & Nhu cầu phân tích báo cáo tài chính doanh nghiệp.
  - 1.2. Hạn chế của mô hình ngôn ngữ lớn (LLM) và RAG truyền thống trên dữ liệu tài chính đa phương thức.
  - 1.3. Mục tiêu, phạm vi và đóng góp mới của đồ án.
- **Chương 2: Cơ sở lý thuyết**
  - 2.1. Mô hình ngôn ngữ lớn (LLM) và bài toán ảo giác (Hallucination).
  - 2.2. Kiến trúc Retrieval-Augmented Generation (Naive RAG, Advanced RAG, Agentic RAG).
  - 2.3. Kỹ thuật Tìm kiếm lai (Hybrid Search: Dense Vector + BM25 & Reciprocal Rank Fusion) và Cross-Encoder Reranking.
  - 2.4. Kỹ thuật bóc tách bố cục tài liệu (Document Layout Analysis) và kết hợp Text-to-SQL.
  - 2.5. Các độ đo đánh giá hệ thống RAG (RAGAS Framework: Faithfulness, Answer Relevancy, Context Precision/Recall).
- **Chương 3: Phân tích và Thiết kế Hệ thống FinSight AI**
  - 3.1. Phân tích yêu cầu chức năng, phi chức năng và phân quyền RBAC.
  - 3.2. Kiến trúc tổng thể hệ thống (Pipeline Nạp dữ liệu & Pipeline Truy vấn Agentic).
  - 3.3. Thiết kế chiến lược Parent-Child Chunking bảo toàn cấu trúc bảng biểu tài chính.
  - 3.4. Thiết kế cơ sở dữ liệu (PostgreSQL & Qdrant Payload Schema).
  - 3.5. Thiết kế máy trạng thái hữu hạn (State Graph) cho Agent tự kiểm tra và sửa lỗi.
- **Chương 4: Cài đặt và Thực nghiệm Đánh giá**
  - 4.1. Môi trường cài đặt và bộ dữ liệu thực nghiệm (Báo cáo thường niên / BCTC).
  - 4.2. Kịch bản thực nghiệm đối chứng (Ablation Study: Naive RAG vs. Hybrid RAG vs. FinSight Agentic RAG).
  - 4.3. Kết quả đánh giá định lượng (Bảng điểm độ chính xác số liệu, RAGAS metrics, độ trễ).
  - 4.4. Minh họa kết quả chạy thực tế trên giao diện ứng dụng.
- **Chương 5: Kết luận và Hướng phát triển**
  - 5.1. Kết quả đạt được.
  - 5.2. Hạn chế hiện tại.
  - 5.3. Hướng phát triển (GraphRAG, Real-time Market Data, Fine-tuning Reranker).

---

## 2. Bộ Câu hỏi Phản biện Thường gặp & Cách Trả lời (Defense & Interview Prep)

### Câu 1: Tại sao không đưa nguyên file PDF 150 trang vào cửa sổ ngữ cảnh (Long Context) của Gemini/GPT mà phải làm RAG phức tạp?
- **Chi phí & Độ trễ (Cost & Latency):** Đưa hàng trăm trang PDF vào mỗi câu hỏi tốn hàng trăm nghìn token/lượt, thời gian chờ 15–30 giây và chi phí tăng gấp hàng chục lần khi có nhiều người dùng.
- **Hiện tượng "Lost in the Middle":** Khi ngữ cảnh quá dài hoặc hỏi so sánh giữa 5–10 báo cáo qua nhiều năm (tổng cộng hàng nghìn trang), LLM dễ bỏ sót chi tiết ở giữa tài liệu.
- **Kiểm soát phân quyền (RBAC) & Kiểm toán:** Chia nhỏ và lưu trữ có cấu trúc giúp lọc quyền truy cập tới từng tài liệu/trang và trích dẫn chính xác vị trí bảng số liệu.

### Câu 2: Tại sao phải kết hợp cả Text-to-SQL (PostgreSQL) lẫn Vector DB (Qdrant)?
- **Vector DB mạnh về ngữ nghĩa nhưng yếu về tính toán tổng hợp:** Nếu hỏi *"Tốc độ tăng trưởng lợi nhuận ròng trung bình của FPT giai đoạn 2021–2024"*, tìm kiếm vector có thể lấy thiếu 1 năm hoặc LLM cộng chia nhẩm bị sai.
- **Text-to-SQL + Python Calculator** đảm bảo các con số kế toán được lưu chuẩn hóa và tính toán chính xác 100%, trong khi **Hybrid RAG** giải quyết phần giải thích nguyên nhân kinh doanh từ văn bản thuyết minh.

### Câu 3: Tại sao dùng Parent-Child Chunking thay vì cắt đoạn 500 ký tự thông thường?
- Các bảng tài chính (như Bảng kết quả kinh doanh) thường dài 800–1500 tokens. Nếu cắt cứng 500 ký tự sẽ làm mất dòng tiêu đề cột (Năm 2023, Năm 2024, Đơn vị tính), khiến những dòng bên dưới trở nên vô nghĩa.
- **Parent-Child Chunking** dùng đoạn nhỏ (Child) để tìm kiếm chính xác cao (High Precision), nhưng khi đưa vào LLM thì trả về toàn bộ bảng gốc (Parent) để giữ trọn vẹn ngữ cảnh (High Recall).

---

## 3. Nhật ký Quyết định Thiết kế & Kết quả Thực nghiệm (Cập nhật trong quá trình làm)

| Ngày | Hạng mục | Quyết định / Thay đổi | Lý do & Kết quả đo lường |
| :--- | :--- | :--- | :--- |
| Khởi tạo | Kiến trúc tổng thể | Chọn Hybrid Agentic RAG + Text-to-SQL | Đảm bảo độ chính xác cho cả số liệu định lượng lẫn văn bản thuyết minh định tính. |
