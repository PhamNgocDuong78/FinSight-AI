# Lộ trình Triển khai & Danh sách Công việc (Master Task List) — FinSight AI

Tài liệu này chia dự án thành **7 Giai đoạn (Phases)** từ cơ bản đến nâng cao.
- Mỗi task được thiết kế vừa vặn cho **1 phiên làm việc với AI**.
- Kèm theo **Prompt mẫu** để bạn chỉ cần copy-paste và điều chỉnh nhẹ khi bắt đầu mỗi bước.
- Khi làm xong bước nào và chạy test thành công, hãy đánh dấu `[x]` vào ô tương ứng.

---

## Giai đoạn 0: Khởi tạo Hạ tầng & Khung Dự án (Foundation Setup)

- [x] **Task 0.1: Khởi tạo Docker Compose & Cấu hình Môi trường**
  - **Mục tiêu:** Tạo `docker-compose.yml` (gồm PostgreSQL 16, Qdrant, Redis), `.env.example`, `.gitignore` và kiểm tra các container chạy ổn định.
  - **Tiêu chí nghiệm thu:** Chạy `docker compose up -d` thành công; kết nối được tới Postgres (port 5432), Qdrant Dashboard (port 6333/dashboard) và Redis (port 6379).
  - **Prompt mẫu:**
    > "Hãy đọc `AGENTS.md` và `docs/ARCHITECTURE.md`. Thực hiện **Task 0.1**: Tạo `docker-compose.yml` cho PostgreSQL 16, Qdrant, Redis kèm healthcheck, tạo `.env.example` và `.gitignore` chuẩn cho Python + Next.js."

- [x] **Task 0.2: Dựng khung Backend FastAPI & Kết nối Cơ sở dữ liệu**
  - **Mục tiêu:** Khởi tạo thư mục `backend/`, `pyproject.toml`, cấu hình `app/core/config.py` (Pydantic Settings), kết nối SQLAlchemy Async + Qdrant Client + endpoint `/api/v1/health`.
  - **Tiêu chí nghiệm thu:** Chạy server FastAPI và viết `tests/integration/test_health.py` kiểm tra kết nối thật tới Postgres, Qdrant, Redis đều trả về `ok`.
  - **Prompt mẫu:**
    > "Đọc `AGENTS.md` và `docs/ARCHITECTURE.md`. Thực hiện **Task 0.2**: Khởi tạo khung `backend/` với FastAPI, `pyproject.toml`, `app/core/config.py`, kết nối async tới PostgreSQL, Qdrant, Redis và viết test kiểm tra `/api/v1/health`."

- [x] **Task 0.3: Thiết kế ORM Models & Database Migration (Alembic)**
  - **Mục tiêu:** Tạo các bảng `users`, `documents`, `parent_chunks`, `financial_metrics`, `chat_sessions`, `chat_messages` đúng như thiết kế trong `docs/ARCHITECTURE.md`.
  - **Tiêu chí nghiệm thu:** Chạy `alembic upgrade head` tạo bảng thành công trong PostgreSQL; có script seed sẵn 3 tài khoản mẫu ứng với 3 role (`admin`, `analyst`, `viewer`).

---

## Giai đoạn 1: Xây dựng Pipeline Nạp Tài liệu Đa phương thức & ETL (Ingestion & ETL)

- [ ] **Task 1.1: Xây dựng Bộ bóc tách PDF giữ nguyên cấu trúc Bảng (Layout-aware Parser)**
  - **Mục tiêu:** Viết module `app/services/ingestion/pdf_parser.py` đọc file PDF báo cáo tài chính, trích xuất văn bản theo từng trang và chuyển các bảng số liệu thành định dạng Markdown chuẩn (không bị lệch cột).
  - **Tiêu chí nghiệm thu:** Thử nghiệm với 1 file PDF báo cáo tài chính thật trong `data/raw_pdfs/`, xuất kết quả ra `data/processed_md/` và kiểm tra bảng cân đối kế toán/kết quả kinh doanh giữ nguyên hàng cột.
  - **Prompt mẫu:**
    > "Đọc `AGENTS.md` và `docs/ARCHITECTURE.md`. Thực hiện **Task 1.1**: Xây dựng `app/services/ingestion/pdf_parser.py` có khả năng bóc tách PDF theo trang, giữ nguyên cấu trúc bảng biểu sang Markdown. Viết unit test và script chạy thử với file PDF mẫu."

- [ ] **Task 1.2: Chiến lược Parent-Child Chunking cho Văn bản & Bảng Tài chính**
  - **Mục tiêu:** Viết `app/services/ingestion/chunker.py`. Với bảng biểu: giữ nguyên cả bảng làm `Parent Chunk`, đồng thời tạo `Child Chunk` chứa tiêu đề bảng + các chỉ tiêu chính để tìm kiếm không bị cắt đôi bảng. Với văn bản: chia Parent (~1000 tokens) và Child (~300 tokens).
  - **Tiêu chí nghiệm thu:** Unit test `tests/unit/test_chunker.py` chứng minh không có bảng Markdown nào bị cắt gãy giữa chừng ở tầng Parent Chunk và mọi Child Chunk đều trỏ đúng `parent_chunk_id`.

- [ ] **Task 1.3: Trích xuất Chỉ số Tài chính Có cấu trúc vào PostgreSQL (Financial ETL)**
  - **Mục tiêu:** Viết `app/services/ingestion/financial_etl.py` sử dụng Structured Output của LLM (Pydantic Schema) kết hợp quy tắc kiểm tra để rút trích các chỉ số cốt lõi (`REVENUE`, `GROSS_PROFIT`, `NET_INCOME`, `TOTAL_ASSETS`, `TOTAL_EQUITY`, `ROE`, `ROA`, `EPS`) từ các bảng tài chính vào bảng `financial_metrics`.
  - **Tiêu chí nghiệm thu:** Sau khi nạp báo cáo PDF, bảng `financial_metrics` trong PostgreSQL có đầy đủ các dòng chỉ số chuẩn kèm `source_page` chính xác.

- [ ] **Task 1.4: Lập chỉ mục Dual Indexing (Dense + Sparse BM25) vào Qdrant & API Upload**
  - **Mục tiêu:** Tạo collection `finsight_child_chunks` trên Qdrant hỗ trợ cả Dense Vector (Embedding) và Sparse Vector (BM25), gắn đầy đủ Payload (`ticker`, `fiscal_year`, `access_level`, `page_number`). Hoàn thiện API `POST /api/v1/documents/upload`.
  - **Tiêu chí nghiệm thu:** Upload 1 file PDF qua API -> lưu Parent Chunks + Financial Metrics vào Postgres và Child Chunks vào Qdrant thành công.

---

## Giai đoạn 2: Xây dựng Hệ thống Truy xuất Lai & Phân quyền (Hybrid Retrieval & RBAC)

- [ ] **Task 2.1: Bộ truy xuất Hybrid Search (Dense + BM25) tích hợp Bộ lọc RBAC**
  - **Mục tiêu:** Viết `app/services/retrieval/hybrid_retriever.py` kết hợp tìm kiếm ngữ nghĩa và từ khóa bằng RRF (Reciprocal Rank Fusion), tự động lọc theo `access_level` của người dùng, `ticker` và `fiscal_year`, sau đó map ngược từ Child Chunk sang Parent Chunk để lấy đầy đủ bảng/ngữ cảnh.
  - **Tiêu chí nghiệm thu:** Test chứng minh: (1) User có role `viewer` không thể truy xuất tài liệu `internal_finance`; (2) Truy vấn chứa mã chỉ tiêu hoặc năm cụ thể lấy đúng bảng ở trang tương ứng.

- [ ] **Task 2.2: Tích hợp Bộ xếp hạng lại (Reranker)**
  - **Mục tiêu:** Viết `app/services/retrieval/reranker.py` chấm điểm lại Top-20 ứng viên từ Hybrid Search xuống Top-5 đoạn ngữ cảnh sát câu hỏi nhất.
  - **Tiêu chí nghiệm thu:** Có log/số liệu so sánh thứ hạng kết quả trước và sau khi Rerank.

---

## Giai đoạn 3: Công cụ Truy vấn Số liệu & Tính toán Tài chính (Text-to-SQL & Financial Tools)

- [ ] **Task 3.1: Công cụ Truy vấn Chỉ số Tài chính (Structured SQL Query Tool)**
  - **Mục tiêu:** Viết `app/agents/tools/sql_tool.py` cho phép chuyển câu hỏi định lượng (ví dụ: *"Doanh thu và lợi nhuận sau thuế của FPT từ 2022 đến 2024"*) thành truy vấn an toàn (Read-only) trên bảng `financial_metrics` kèm kiểm tra phân quyền.
  - **Tiêu chí nghiệm thu:** Chỉ cho phép câu lệnh `SELECT` hợp lệ hoặc truy vấn qua bộ lọc tham số hóa; trả về đúng con số, đơn vị tính và số trang trích dẫn (`source_page`).

- [ ] **Task 3.2: Công cụ Tính toán Tài chính Chính xác (Financial Calculator Tool)**
  - **Mục tiêu:** Viết `app/agents/tools/calculator_tool.py` thực hiện các phép tính: Tăng trưởng YoY (%), CAGR, Biên lợi nhuận, Tỷ trọng cơ cấu... bằng Python chuẩn xác tuyệt đối.
  - **Tiêu chí nghiệm thu:** Unit test kiểm tra các phép tính tài chính trả về kết quả làm tròn chuẩn 2 chữ số thập phân, xử lý an toàn trường hợp chia cho 0.

---

## Giai đoạn 4: Điều phối Agentic Workflow bằng LangGraph (Agentic Orchestration)

- [ ] **Task 4.1: Xây dựng Node Phân tích Câu hỏi & Định tuyến (Rewrite & Intent Router)**
  - **Mục tiêu:** Viết `app/agents/nodes/router.py` làm nhiệm vụ: viết lại câu hỏi dựa trên lịch sử hội thoại, bóc tách thực thể (`target_tickers`, `target_years`) và phân loại intent (`sql`, `rag`, `hybrid`, `chitchat`).
  - **Tiêu chí nghiệm thu:** Bộ test với 15 câu hỏi mẫu phân loại đúng intent >= 90%.

- [ ] **Task 4.2: Xây dựng Node Tự kiểm tra & Sửa truy vấn (Relevance Grader & Self-Correction)**
  - **Mục tiêu:** Viết `app/agents/nodes/grader.py` đánh giá xem các đoạn ngữ cảnh tìm được có trả lời được câu hỏi hay không. Nếu chưa đủ, tự sinh câu truy vấn mới để tìm lại (tối đa 2 lần). Nếu không có trong tài liệu, kích hoạt luồng từ chối bịa đặt.
  - **Tiêu chí nghiệm thu:** Test với câu hỏi không có trong tài liệu -> Agent trả lời rõ là không tìm thấy dữ liệu trong báo cáo hiện có, không tự bịa số liệu.

- [ ] **Task 4.3: Xây dựng Node Tổng hợp Câu trả lời kèm Trích dẫn & Lắp ráp LangGraph**
  - **Mục tiêu:** Viết `app/agents/nodes/synthesizer.py` và `app/agents/graph.py` kết nối toàn bộ luồng. Đảm bảo mọi con số và nhận định đều gắn kèm trích dẫn `[Ticker_Năm, tr. X]`. Hoàn thiện API Chat Streaming (`POST /api/v1/chat/stream`).
  - **Tiêu chí nghiệm thu:** Gọi API trả về SSE stream mượt mà gồm cả: (1) Các bước suy luận của Agent (`reasoning_steps`), (2) Nội dung câu trả lời, và (3) Mảng `citations` có cấu trúc để Frontend mở đúng trang PDF.

---

## Giai đoạn 5: Xây dựng Giao diện Web & Trình đối chiếu PDF (Frontend Workspace)

- [ ] **Task 5.1: Khởi tạo Next.js App & Giao diện Chat Tài chính**
  - **Mục tiêu:** Dựng giao diện Chat hỗ trợ hiển thị Markdown Table đẹp mắt, hiển thị tiến trình suy luận của Agent theo thời gian thực (giống kiểu "Thinking steps") và các thẻ trích dẫn bấm được.

- [ ] **Task 5.2: Khung Đối chiếu PDF Tương tác (Interactive Citation PDF Viewer)**
  - **Mục tiêu:** Xây dựng khung xem PDF bên phải màn hình. Khi người dùng bấm vào trích dẫn `[FPT_2024, tr. 45]`, trình xem PDF tự động mở báo cáo `FPT_2024.pdf` và nhảy tới đúng trang 45.

- [ ] **Task 5.3: Màn hình Quản lý Báo cáo, Xem Bảng Chỉ số ETL & Chuyển đổi Role (RBAC Demo)**
  - **Mục tiêu:** Trang quản lý cho phép upload PDF mới, xem các chỉ số tài chính đã rút trích tự động vào bảng `financial_metrics`, và nút chuyển nhanh giữa 3 Role (`Admin`, `Analyst`, `Viewer`) trên thanh công cụ để dễ dàng demo khi bảo vệ đồ án.

---

## Giai đoạn 6: Thực nghiệm, Đánh giá Khoa học & Hoàn thiện Đồ án (Evaluation & Polish)

- [ ] **Task 6.1: Xây dựng Bộ Dữ liệu Kiểm thử Chuẩn (Golden Evaluation Dataset)**
  - **Mục tiêu:** Tạo file `data/eval_datasets/golden_qa.json` gồm 60–100 câu hỏi chia làm 4 nhóm:
    1. Câu hỏi tra cứu văn bản đơn giản (25%).
    2. Câu hỏi đọc bảng biểu & tính toán tài chính (30%).
    3. Câu hỏi phân tích đa bước kết hợp số liệu + thuyết minh (30%).
    4. Câu hỏi ngoài phạm vi / bẫy chống bịa đặt (15%).

- [ ] **Task 6.2: Viết Pipeline Chấm điểm Tự động (Ablation Study Benchmark)**
  - **Mục tiêu:** Viết `backend/evaluation/run_benchmark.py` chạy tự động bộ câu hỏi trên 3 cấu hình:
    - *Config A:* Naive RAG (chỉ Vector Search, cắt chunk cố định).
    - *Config B:* Hybrid RAG (Parent-Child + Dense/BM25 + Reranker).
    - *Config C:* FinSight Agentic RAG (Hybrid RAG + Text-to-SQL + Self-Correction).
  - **Tiêu chí nghiệm thu:** Xuất ra bảng thống kê Markdown/CSV so sánh các chỉ số `Faithfulness`, `Answer Relevancy`, `Context Precision`, `Numeric Accuracy` và `Latency` để đưa thẳng vào chương Thực nghiệm của báo cáo tốt nghiệp.

---

## Giai đoạn 7: Mở rộng Nâng cao (Tùy chọn sau khi xong cốt lõi)

- [ ] **Task 7.1:** Tích hợp **Semantic Cache (Redis)** để trả lời tức thì (< 50ms) các câu hỏi tương tự đã được hỏi trước đó.
- [ ] **Task 7.2:** Tích hợp công cụ lấy dữ liệu thị trường chứng khoán thời gian thực (`MarketDataTool`).
- [ ] **Task 7.3:** Tính năng xuất cuộc phân tích thành báo cáo tổng hợp PDF/Markdown chuyên nghiệp.
