# 포스터용 QR 코드 생성기
# 사용법:  python gen_qr.py https://ada2026-qa.onrender.com
# 결과: 바탕화면에 ada2026_qa_qr.png 저장 (고해상도, 포스터 인쇄용)
#
# qrcode 미설치 시:  pip install "qrcode[pil]"

import sys, os

def main():
    if len(sys.argv) < 2:
        print("사용법: python gen_qr.py <배포_URL>")
        print("예시:   python gen_qr.py https://ada2026-qa.onrender.com")
        sys.exit(1)

    url = sys.argv[1].strip()
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_H
    except ImportError:
        print("qrcode 모듈이 없습니다. 설치:  pip install \"qrcode[pil]\"")
        sys.exit(1)

    qr = qrcode.QRCode(
        version=None,
        error_correction=ERROR_CORRECT_H,  # 30% 복원 - 포스터 손상에도 강함
        box_size=20,                        # 인쇄용 고해상도
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    out = os.path.join(desktop, "ada2026_qa_qr.png")
    img.save(out)
    print(f"QR 저장 완료: {out}")
    print(f"대상 URL    : {url}")
    print("포스터에 인쇄하고 'Scan to ask the presenter — live reply' 문구를 함께 넣으세요.")

if __name__ == "__main__":
    main()
