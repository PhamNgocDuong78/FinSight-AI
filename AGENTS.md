# AGENTS.md — Quy tắc & Tiêu chuẩn Kỹ thuật cho AI Assistant

Tài liệu này định nghĩa các quy tắc bắt buộc khi AI hỗ trợ viết code, thiết kế hoặc sửa lỗi cho dự án **FinSight AI**. Mọi đề xuất và đoạn mã sinh ra phải tuân thủ nghiêm ngặt các nguyên tắc dưới đây để đảm bảo dự án sạch, dễ hiểu khi bảo vệ đồ án và dễ mở rộng.

---

## 1. Nguyên tắc Cốt lõi (Core Engineering Principles)

1. **Không viết code khi chưa rõ thiết kế:**
   - Trước khi tạo hoặc sửa nhiều file, hãy đọc `docs/SPEC.md`, `docs/ARCHITECTURE.md` và `docs/TASKS.md`.
   - Nếu yêu cầu có điểm mơ hồ, hãy hỏi lại hoặc nêu phương án ngắn gọn trước khi code.
2. **Kiến trúc Module hóa & Dễ cắm ghép (Modular & Pluggable Design):**
   - Sử dụng **Dependency Injection** và **Interface/Protocol (Python `Protocol` hoặc `ABC`)** cho các thành phần dễ thay đổi: LLM Provider, Embedding Model, PDF Parser, Vector Store, Reranker.
   - Tuyệt đối không hardcode chặt một nhà cung cấp LLM duy nhất vào tầng nghiệp vụ (Business Logic). Mọi cấu hình model/API key phải đi qua `app/core/config.py` (dùng `pydantic-settings`).
3. **Chia nhỏ File & Trách nhiệm đơn lẻ (Single Responsibility):**
   - Không viết một file code vượt quá ~250–300 dòng.
   - Tách biệt rõ ràng:
     - `api/` (Routing, HTTP Request/Response validation)
     - `services/` (Business logic, Ingestion, Retrieval)
     - `agents/` (LangGraph nodes, state, tools, prompts)
     - `repositories/` hoặc `db/` (Truy vấn PostgreSQL, Qdrant, Redis)
     - `schemas/` (Pydantic models cho dữ liệu đầu vào/đầu ra)
4. **Chính xác với Số liệu Tài chính (Zero-Hallucination on Numbers):**
   - LLM **không được phép tự tính nhẩm** các phép tính tài chính phức tạp (tăng trưởng, tỷ trọng, ROE/ROA). Bắt buộc phải truy vấn qua công cụ SQL (`sql_tool`) hoặc công cụ tính toán Python (`calculator_tool`).
   - Mọi câu trả lời của hệ thống phải kèm theo `citations` (tên file, số trang, mã đoạn/bảng).

---

## 2. Tech Stack Chuẩn (Pinned Stack)

- **Ngôn ngữ:** Python 3.11+ (Backend & AI Pipeline), TypeScript (Frontend Next.js App Router).
- **Quản lý gói Python:** `uv` (ưu tiên) hoặc `pip` với `pyproject.toml`.
- **Backend Framework:** `FastAPI` + `Pydantic v2` + `SQLAlchemy 2.0 (async)` + `Alembic` (migration).
- **AI & Orchestration:**
  - `langgraph` (xây dựng luồng Agent theo State Graph rõ ràng, dễ kiểm soát).
  - `google-genai` (SDK chính thức mới nhất cho Gemini API) và hỗ trợ mở rộng sang OpenAI/Ollama qua lớp adapter.
  - `docling` hoặc `pymupdf4llm` (bóc tách PDF giữ nguyên cấu trúc bảng Markdown).
- **Cơ sở dữ liệu & Hạ tầng (Docker Compose):**
  - `PostgreSQL 16`: Lưu dữ liệu người dùng, phân quyền RBAC, lịch sử chat, metadata tài liệu và **bảng chỉ số tài chính chuẩn hóa (Structured Financial Tables)**.
  - `Qdrant`: Lưu trữ Vector Embeddings + Sparse Vectors (BM25) phục vụ Hybrid Search.
  - `Redis`: Caching kết quả truy vấn và quản lý hàng đợi tác vụ nền (Background Ingestion).
  - `Langfuse` (tùy chọn ở giai đoạn tối ưu): Theo dõi trace, độ trễ và chi phí token.
- **Đánh giá (Evaluation):** `ragas` + `pytest`.

> **Lưu ý quan trọng về SDK:** Khi sử dụng Gemini API trong Python, luôn dùng gói `google-genai` mới (`from google import genai`), **không dùng** gói cũ `google-generativeai` đã ngừng phát triển.

---

## 3. Quy chuẩn Viết Code (Coding Standards)

### Python (Backend & AI)
- **Type Hints bắt buộc:** Mọi hàm và phương thức phải khai báo đầy đủ kiểu dữ liệu đầu vào và đầu ra.
- **Async/Await:** Sử dụng `async def` cho các endpoint FastAPI và các tác vụ I/O (gọi LLM, truy vấn DB, Qdrant).
- **Quản lý Prompt:** Không nhồi các đoạn prompt dài vào giữa file logic. Tất cả System Prompts phải được đặt tập trung và có đánh phiên bản tại `backend/app/agents/prompts/`.
- **Xử lý lỗi (Error Handling) & Logging:**
  - Dùng `structlog` hoặc `logging` chuẩn có kèm `request_id` / `trace_id`.
  - Không dùng `except Exception: pass` làm ẩn lỗi. Phải log rõ nguyên nhân và trả về mã lỗi chuẩn.

### TypeScript / Next.js (Frontend)
- Sử dụng TypeScript strict mode, không dùng `any`.
- Tách biệt UI components (`components/ui/`), Feature components (`components/chat/`, `components/pdf-viewer/`) và API client (`lib/api/`).

---

## 4. Quy tắc Kiểm thử & Nghiệm thu (Testing Rules)

1. Mỗi khi hoàn thành một module trong `docs/TASKS.md`, phải viết kèm bài test trong `backend/tests/`.
2. Phân tách rõ:
   - `tests/unit/`: Kiểm thử logic tách chunk, chuẩn hóa bảng, tính toán chỉ số (chạy nhanh, không tốn API quota).
   - `tests/integration/`: Kiểm thử kết nối DB, Qdrant, luồng Agent thật.
3. **Không xóa hoặc làm yếu điều kiện `assert`** trong bài test chỉ để làm test pass. Hãy sửa lỗi ở code logic.

---

## 5. Quy tắc Giải thích cho Sinh viên (Educational Rule)

Vì đây là **Đồ án Tốt nghiệp** và dùng để **Phỏng vấn xin việc**, sau mỗi lần hoàn thành một module quan trọng, AI cần tóm tắt ngắn gọn:
1. **Luồng hoạt động chính** của đoạn code vừa viết.
2. **Lý do chọn thiết kế/thuật toán đó** (Design Trade-offs) để sinh viên nắm vững bản chất khi trả lời câu hỏi phản biện của hội đồng.

---

## 6. Quy tắc Quản lý Phiên bản (Git & GitHub Workflow)

- **Repository chính thức:** `https://github.com/PhamNgocDuong78/FinSight-AI.git`
- **Quy tắc Commit & Push theo Task:**
  1. Ngay sau khi hoàn thành một Task trong `docs/TASKS.md` (đã kiểm thử và đánh dấu `[x]`), **bắt buộc** phải thực hiện `git add`, `git commit` và `git push` lên nhánh chính của GitHub.
  2. Sử dụng thông điệp commit rõ ràng theo chuẩn Conventional Commits (ví dụ: `feat: complete task 0.1 - docker compose and env setup`).
  3. Luôn kiểm tra `git status` trước khi commit để đảm bảo không đẩy nhầm file `.env`, API key hoặc file dữ liệu PDF nặng đã được loại trừ trong `.gitignore`.
