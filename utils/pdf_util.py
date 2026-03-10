from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import os


def save_pdf(data: dict, filename: str, folder="memory"):
    """
    Save dictionary data into a readable PDF document.
    """

    os.makedirs(folder, exist_ok=True)

    filepath = os.path.join(folder, filename)

    c = canvas.Canvas(filepath, pagesize=letter)

    y = 750

    for key, value in data.items():

        line = f"{key}: {value}"

        for chunk in [line[i:i+90] for i in range(0, len(line), 90)]:
            c.drawString(50, y, chunk)
            y -= 15

        y -= 10

        if y < 50:
            c.showPage()
            y = 750

    c.save()

    print(f"✅ Saved PDF → {filepath}")

    return filepath