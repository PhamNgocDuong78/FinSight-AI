# Đặc tả Hệ thống (System Specification) — FinSight AI

## 1. Bối cảnh & Bài toán (Problem Statement)

Trong lĩnh vực tài chính và quản trị doanh nghiệp, các báo cáo thường niên (Annual Reports), báo cáo tài chính hợp nhất (Financial Statements) và báo cáo phân tích chứa lượng thông tin khổng lồ dưới hai dạng:
1. **Dữ liệu có cấu trúc / bán cấu trúc:** Bảng cân đối kế toán, Báo cáo kết quả hoạt động kinh doanh, Báo cáo lưu chuyển tiền tệ, các bảng thuyết minh chi tiết.
2. **Dữ liệu phi cấu trúc:** Nhận định của Ban điều hành, chiến lược phát triển, phân tích rủi ro thị trường, biểu đồ minh họa.

### Hạn chế của các hệ thống Chatbot RAG truyền thống
- **Vỡ cấu trúc bảng biểu:** Cắt văn bản theo độ dài cố định làm đứt gãy các hàng/cột của bảng tài chính, dẫn đến truy xuất sai số liệu.
- **Tính toán sai lệch:** LLM tự cộng trừ hoặc tính phần trăm tăng trưởng dựa trên văn bản thường dẫn đến sai số (Hallucination).
- **Truy xuất kém với thuật ngữ chuyên ngành:** Chỉ dùng Vector Search thường bỏ sót các mã cổ phiếu, mã chỉ tiêu kế toán, số hiệu thông tư hoặc năm tài chính cụ thể.
- **Thiếu minh bạch & phân quyền:** Không chỉ ra được con số lấy từ trang nào, bảng nào và không kiểm soát được quyền truy cập tài liệu nội bộ theo phòng ban.

---

## 2. Mục tiêu của Hệ thống

Xây dựng **FinSight AI** — một nền tảng trợ lý phân tích tài chính thông minh có khả năng:
1. Tiếp nhận và bóc tách chuẩn xác tài liệu PDF đa phương thức (văn bản, bảng biểu phức tạp, biểu đồ), hỗ trợ cả báo cáo tiếng Việt (FPT, VNM, VCB, HPG...) và tiếng Anh (Apple, NVIDIA, bộ dữ liệu FinanceBench).
2. Kết hợp nhuần nhuyễn giữa **Truy vấn dữ liệu có cấu trúc (Text-to-SQL)** cho số liệu tài chính và **Hybrid RAG** cho văn bản thuyết minh.
3. Sử dụng kiến trúc **Agentic Workflow (LangGraph)** có khả năng lập kế hoạch truy vấn đa bước, tự đánh giá chất lượng ngữ cảnh tìm được và tự sửa câu truy vấn.
4. Cung cấp giao diện trực quan cho phép đối chiếu câu trả lời của AI với đúng trang tài liệu PDF gốc.
5. Cung cấp bộ công cụ đánh giá định lượng (Evaluation Pipeline) để chứng minh hiệu quả khoa học của đề tài.

---

## 3. Đối tượng Người dùng & Phân quyền (RBAC)

| Vai trò (Role) | Quyền hạn chính |
| :--- | :--- |
| **Admin / Data Manager** | Tải lên báo cáo PDF, cấu hình quyền truy cập tài liệu (`Public`, `Internal_Finance`, `Executive_Only`), kiểm tra kết quả bóc tách bảng/ETL, theo dõi dashboard đánh giá và chi phí token. |
| **Financial Analyst (Chuyên viên phân tích)** | Truy cập tài liệu `Public` và `Internal_Finance`; sử dụng trợ lý AI để hỏi đáp sâu, so sánh chỉ số đa công ty/đa kỳ, xuất báo cáo tổng hợp có trích dẫn. |
| **Viewer / Guest (Người dùng cơ bản)** | Chỉ được hỏi đáp và tra cứu trên các báo cáo công khai (`Public`). Khi hỏi về tài liệu mật, hệ thống từ chối truy xuất ngay từ tầng Database/Vector Filter. |

---

## 4. Yêu cầu Chức năng Chi tiết (Functional Requirements)

### Phân hệ 1: Quản lý & Xử lý Tài liệu Đa phương thức (Multimodal Ingestion & ETL)
- **FR-1.1 (Upload & Metadata):** Cho phép tải lên file PDF báo cáo tài chính, gắn nhãn: Mã doanh nghiệp (`ticker`, ví dụ: `FPT`, `AAPL`), Năm tài chính (`fiscal_year`), Kỳ báo cáo (`Q1`, `Q2`, `Q3`, `Q4`, `FY`), Ngôn ngữ (`vi`, `en`), Mức phân quyền (`access_level`).
- **FR-1.2 (Layout-aware PDF Parsing):** Bóc tách văn bản và giữ nguyên cấu trúc bảng biểu dưới dạng Markdown/JSON kèm tọa độ trang (`page_number`).
- **FR-1.3 (Chart/Image Summarization):** Nhận diện hình ảnh/biểu đồ quan trọng trong báo cáo và dùng Vision LLM sinh mô tả chi tiết số liệu trong biểu đồ để phục vụ tìm kiếm.
- **FR-1.4 (Structured Financial ETL):** Trích xuất các chỉ tiêu tài chính cốt lõi (Doanh thu thuần, Lợi nhuận gộp, Lợi nhuận sau thuế, Tổng tài sản, Vốn chủ sở hữu, EPS, ROE, ROA...) lưu vào bảng chuẩn `financial_metrics` trong PostgreSQL.
- **FR-1.5 (Parent-Child Chunking & Dual Indexing):** Chia tài liệu thành các đoạn nhỏ (Child Chunks ~300 tokens) liên kết với đoạn lớn/toàn bảng (Parent Chunks ~1000–1500 tokens), lập chỉ mục đồng thời Dense Vector và Sparse BM25 trong Qdrant.

### Phân hệ 2: Hỏi đáp Thông minh (Agentic RAG & Text-to-SQL Pipeline)
- **FR-2.1 (Query Understanding & Routing):**
  - Tự động làm rõ câu hỏi dựa trên lịch sử hội thoại (Conversational Query Rewriting).
  - Phân loại câu hỏi vào 1 trong 4 luồng xử lý:
    1. `QUANTITATIVE_SQL`: Câu hỏi về con số, tính toán tỷ số, so sánh tăng trưởng qua các năm.
    2. `QUALITATIVE_RAG`: Câu hỏi về nguyên nhân, chiến lược, rủi ro, thuyết minh.
    3. `HYBRID_ANALYTICAL`: Câu hỏi cần cả tính toán số liệu lẫn giải thích nguyên nhân (phân rã thành các sub-queries chạy song song).
    4. `CHITCHAT_OR_OUT_OF_SCOPE`: Chào hỏi hoặc câu hỏi ngoài phạm vi.
- **FR-2.2 (Hybrid Retrieval & Reranking):**
  - Áp dụng bộ lọc Metadata & RBAC (`ticker`, `fiscal_year`, `access_level`) trước khi tìm kiếm.
  - Kết hợp điểm Dense Vector và Sparse BM25 bằng thuật toán RRF (Reciprocal Rank Fusion).
  - Sử dụng Cross-Encoder Reranker để chọn ra Top-K ngữ cảnh chất lượng cao nhất.
- **FR-2.3 (Self-Correction & Hallucination Guard):**
  - **Relevance Grader:** Đánh giá ngữ cảnh lấy ra có chứa thông tin trả lời câu hỏi hay không. Nếu thiếu, tự động điều chỉnh từ khóa tìm lại (tối đa 2 vòng lặp).
  - **Numeric Verification:** Mọi phép tính phần trăm, tăng trưởng, trung bình đều phải gọi Python Calculator/SQL, không để LLM tự nhẩm.
  - Nếu sau khi tìm kiếm vẫn không có dữ liệu trong kho, trả lời trung thực là không tìm thấy dữ liệu trong báo cáo hiện có.
- **FR-2.4 (Streaming Response & Inline Citations):**
  - Trả lời theo thời gian thực (Server-Sent Events - SSE).
  - Mỗi luận điểm hoặc số liệu phải đính kèm thẻ trích dẫn dạng `[FPT_AR_2024, tr. 45]`.

### Phân hệ 3: Giao diện Người dùng & Trình đối chiếu PDF (Frontend Workspace)
- **FR-3.1 (Chat Interface):** Hỗ trợ hiển thị bảng Markdown, biểu đồ cơ bản, công thức tài chính và trạng thái suy luận của Agent (*"Đang phân tích câu hỏi..."*, *"Đang truy vấn SQL chỉ số FPT 2024..."*, *"Đang đọc thuyết minh trang 45..."*).
- **FR-3.2 (Interactive Citation PDF Viewer):** Khi người dùng nhấp vào thẻ trích dẫn `[FPT_AR_2024, tr. 45]`, khung bên phải tự động mở file PDF đó và cuộn tới đúng trang 45 để kiểm chứng.
- **FR-3.3 (Document & Role Manager):** Màn hình quản lý danh sách báo cáo đã nạp, trạng thái xử lý (`Pending`, `Processing`, `Indexed`, `Failed`) và chuyển đổi nhanh vai trò người dùng để demo tính năng phân quyền RBAC.

### Phân hệ 4: Thực nghiệm & Đánh giá Khoa học (Evaluation Suite)
- **FR-4.1 (Golden Dataset Management):** Quản lý bộ câu hỏi kiểm thử gồm 4 nhóm: Câu hỏi tra cứu đơn giản, Câu hỏi đọc bảng biểu, Câu hỏi suy luận đa bước (Multi-hop), và Câu hỏi bẫy (không có trong tài liệu).
- **FR-4.2 (Ablation Study Benchmark):** Chạy script tự động so sánh 3 cấu hình:
  1. `Baseline`: Naive RAG (Fixed-size chunking + Dense Vector Search).
  2. `Hybrid RAG`: Layout-aware chunking + Hybrid Search (Dense + BM25) + Reranker.
  3. `FinSight Agentic (Proposed)`: Hybrid RAG + Text-to-SQL + Self-Correction Agent.
- **FR-4.3 (Metrics Export):** Xuất báo cáo kết quả ra CSV/Markdown với các chỉ số: `Faithfulness`, `Answer Relevancy`, `Context Precision`, `Context Recall`, `Numeric Accuracy`, `Latency (P50/P95)` và `Token Cost`.

---

## 5. Yêu cầu Phi chức năng (Non-Functional Requirements)

1. **Độ trễ (Latency):** Thời gian phản hồi token đầu tiên (TTFT) dưới 2.5 giây cho câu hỏi thông thường; tổng thời gian suy luận đa bước dưới 12 giây.
2. **Tính mở rộng (Extensibility):** Có thể thêm một công ty mới, thêm một công cụ mới cho Agent (ví dụ: lấy giá cổ phiếu thời gian thực) hoặc đổi LLM model chỉ qua file cấu hình mà không phải sửa lại luồng chính.
3. **Khả năng tái lập (Reproducibility):** Toàn bộ hệ thống Backend, Database, Vector DB, Cache khởi chạy được qua `docker compose up -d`.

---

## 6. Lộ trình Mở rộng trong Tương lai (Future Roadmap)

Thiết kế hiện tại đã chừa sẵn các điểm kết nối (Extension Points) để bạn dễ dàng nâng cấp thêm nếu muốn phát triển tiếp sau đồ án:
- **Mở rộng 1 — GraphRAG (Knowledge Graph):** Kết nối mối quan hệ chuỗi cung ứng, công ty mẹ - công ty con, cổ đông lớn bằng Neo4j.
- **Mở rộng 2 — Real-time Market Data Tool:** Thêm tool gọi API chứng khoán (vnstock / Yahoo Finance) để kết hợp báo cáo quá khứ với giá cổ phiếu hiện tại.
- **Mở rộng 3 — Automated Report Generator:** Tự động xuất báo cáo phân tích tổng hợp một doanh nghiệp ra file PDF/DOCX hoàn chỉnh theo mẫu.
