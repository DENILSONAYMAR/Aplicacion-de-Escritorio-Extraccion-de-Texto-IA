from PIL import Image, ImageOps, ImageFilter
import pytesseract


def extract_text_from_image(file_path: str) -> str:
    image = Image.open(file_path)

    image = ImageOps.exif_transpose(image)

    # intento 1: gris + contraste
    img1 = image.convert("L")
    img1 = ImageOps.autocontrast(img1)
    w1, h1 = img1.size
    img1 = img1.resize((w1 * 3, h1 * 3))
    img1 = img1.filter(ImageFilter.SHARPEN)

    text1 = pytesseract.image_to_string(
        img1,
        lang="spa+eng",
        config="--oem 3 --psm 6"
    ).strip()

    # intento 2: binarizada
    img2 = img1.point(lambda x: 0 if x < 160 else 255, "1")

    text2 = pytesseract.image_to_string(
        img2,
        lang="spa+eng",
        config="--oem 3 --psm 6"
    ).strip()

    # guarda debug
    img1.save(file_path + "_debug_gray.png")
    img2.save(file_path + "_debug_bin.png")

    return text1 if len(text1) >= len(text2) else text2
