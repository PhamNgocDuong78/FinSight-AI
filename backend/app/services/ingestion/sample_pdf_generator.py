"""Helper to generate a realistic multi-page financial statement PDF for testing and demos."""

from pathlib import Path

import pymupdf


def _draw_grid_table(
    page: pymupdf.Page,
    top_y: float,
    col_widths: list[float],
    rows: list[list[str]],
    fontname: str,
    row_height: float = 26.0,
    start_x: float = 25.0,
) -> float:
    """Draw a bordered table with cells onto a PyMuPDF page and return bottom y."""
    total_width = sum(col_widths)
    curr_y = top_y

    for row_idx, row in enumerate(rows):
        curr_x = start_x
        is_header = row_idx == 0
        fill_color = (0.90, 0.94, 0.98) if is_header else None

        for col_idx, cell_text in enumerate(row):
            col_w = col_widths[col_idx]
            rect = pymupdf.Rect(curr_x, curr_y, curr_x + col_w, curr_y + row_height)
            page.draw_rect(
                rect,
                color=(0.2, 0.2, 0.2),
                fill=fill_color,
                width=0.75,
            )
            page.insert_text(
                pymupdf.Point(curr_x + 5, curr_y + 17),
                cell_text,
                fontsize=8.5,
                fontname=fontname,
                color=(0, 0, 0),
            )
            curr_x += col_w
        curr_y += row_height

    outer_rect = pymupdf.Rect(start_x, top_y, start_x + total_width, curr_y)
    page.draw_rect(outer_rect, color=(0.1, 0.1, 0.1), width=1.0)
    return curr_y


def create_sample_financial_pdf(output_path: Path | str) -> Path:
    """Create a 2-page FPT 2024 Financial Statement PDF with Income Statement & Balance Sheet."""
    target = Path(output_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open()
    doc.set_metadata(
        {
            "title": "Báo cáo Tài chính Hợp nhất FPT Năm 2024",
            "author": "CTCP FPT (Ticker: FPT)",
            "subject": "Consolidated Financial Statements FY2024",
        }
    )

    win_font = Path("C:/Windows/Fonts/arial.ttf")
    fontname = "F0" if win_font.exists() else "helv"
    fontfile = str(win_font) if win_font.exists() else None

    # --- PAGE 1: Income Statement ---
    p1 = doc.new_page(width=595, height=842)
    if fontfile:
        p1.insert_font(fontname=fontname, fontfile=fontfile)

    p1.insert_text(
        pymupdf.Point(25, 50),
        "I. BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH HỢP NHẤT NĂM 2024",
        fontsize=12,
        fontname=fontname,
    )
    p1.insert_textbox(
        pymupdf.Rect(25, 65, 570, 115),
        (
            "Trong năm tài chính 2024, Tập đoàn FPT (Mã CK: FPT) ghi nhận doanh thu thuần hợp nhất "
            "đạt 62.849 tỷ VNĐ, tăng trưởng 19,4% so với năm 2023 nhờ động lực chính từ Khối Công nghệ "
            "và Dịch vụ CNTT nước ngoài."
        ),
        fontsize=9.5,
        fontname=fontname,
    )

    income_rows = [
        [
            "Chỉ tiêu",
            "Mã số",
            "Thuyết minh",
            "Năm 2024 (Tỷ VNĐ)",
            "Năm 2023 (Tỷ VNĐ)",
            "Tăng trưởng (%)",
        ],
        [
            "1. Doanh thu thuần về bán hàng và CCDV",
            "10",
            "V.01",
            "62.849",
            "52.618",
            "+19,4%",
        ],
        ["2. Giá vốn hàng bán", "11", "V.02", "38.527", "32.307", "+19,3%"],
        [
            "3. Lợi nhuận gộp về bán hàng và CCDV",
            "20",
            "V.03",
            "24.322",
            "20.311",
            "+19,7%",
        ],
        ["4. Lợi nhuận thuần từ HĐKD", "30", "V.04", "11.071", "9.203", "+20,3%"],
        [
            "5. Tổng lợi nhuận kế toán trước thuế",
            "50",
            "V.05",
            "11.070",
            "9.203",
            "+20,3%",
        ],
        ["6. Lợi nhuận sau thuế TNDN", "60", "V.06", "9.420", "7.788", "+21,0%"],
        [
            "7. Lãi cơ bản trên cổ phiếu (EPS - VNĐ)",
            "70",
            "V.07",
            "4.940",
            "4.650",
            "+6,2%",
        ],
    ]
    bottom_y1 = _draw_grid_table(
        page=p1,
        top_y=125.0,
        col_widths=[190.0, 42.0, 62.0, 86.0, 86.0, 79.0],
        rows=income_rows,
        fontname=fontname,
    )
    p1.insert_textbox(
        pymupdf.Rect(25, bottom_y1 + 20, 570, bottom_y1 + 85),
        (
            "THUYẾT MINH KẾT QUẢ KINH DOANH\n"
            "Biên lợi nhuận gộp năm 2024 đạt 38,7%, cải thiện nhẹ so với mức 38,6% của năm 2023 nhờ tỷ trọng "
            "dịch vụ chuyển đổi số (Digital Transformation) có biên lợi nhuận cao tăng mạnh tại thị trường Nhật Bản và APAC."
        ),
        fontsize=9.5,
        fontname=fontname,
    )

    # --- PAGE 2: Balance Sheet ---
    p2 = doc.new_page(width=595, height=842)
    if fontfile:
        p2.insert_font(fontname=fontname, fontfile=fontfile)

    p2.insert_text(
        pymupdf.Point(25, 50),
        "II. BẢNG CÂN ĐỐI KẾ TOÁN HỢP NHẤT TÓM TẮT TẠI 31/12/2024",
        fontsize=12,
        fontname=fontname,
    )
    p2.insert_textbox(
        pymupdf.Rect(25, 65, 570, 110),
        (
            "Tại thời điểm 31/12/2024, tổng tài sản hợp nhất của FPT đạt 72.015 tỷ VNĐ, tăng 19,5% so với đầu năm. "
            "Cấu trúc tài chính duy trì lành mạnh với lượng tiền và tiền gửi ngắn hạn dồi dào."
        ),
        fontsize=9.5,
        fontname=fontname,
    )

    balance_rows = [
        [
            "Tài sản / Nguồn vốn",
            "Mã số",
            "31/12/2024 (Tỷ VNĐ)",
            "31/12/2023 (Tỷ VNĐ)",
            "Thay đổi (%)",
        ],
        ["A. TÀI SẢN NGẮN HẠN", "100", "46.810", "36.705", "+27,5%"],
        ["B. TÀI SẢN DÀI HẠN", "200", "25.205", "23.578", "+6,9%"],
        ["TỔNG CỘNG TÀI SẢN", "270", "72.015", "60.283", "+19,5%"],
        ["C. NỢ PHẢI TRẢ", "300", "36.415", "30.350", "+20,0%"],
        ["D. VỐN CHỦ SỞ HỮU", "400", "35.600", "29.933", "+18,9%"],
        ["TỔNG CỘNG NGUỒN VỐN", "440", "72.015", "60.283", "+19,5%"],
    ]
    bottom_y2 = _draw_grid_table(
        page=p2,
        top_y=120.0,
        col_widths=[205.0, 55.0, 100.0, 100.0, 85.0],
        rows=balance_rows,
        fontname=fontname,
    )
    p2.insert_textbox(
        pymupdf.Rect(25, bottom_y2 + 20, 570, bottom_y2 + 90),
        (
            "CHỈ SỐ KHẢ NĂNG SINH LỜI NĂM 2024\n"
            "Tỷ suất lợi nhuận trên vốn chủ sở hữu (ROE) đạt 28,7% và tỷ suất lợi nhuận trên tổng tài sản (ROA) đạt 14,2%."
        ),
        fontsize=9.5,
        fontname=fontname,
    )

    doc.save(str(target))
    doc.close()
    return target
