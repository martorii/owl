from src.tools.parser.parser import PdfParser


def test_is_not_inside_table():
    # Define a table bounding box: x0, top, x1, bottom
    table_bboxes = [(100, 100, 200, 200)]

    # 1. Object outside (left)
    obj_outside_left = {
        "object_type": "char",
        "x0": 50,
        "top": 150,
        "x1": 60,
        "bottom": 160,
    }
    assert PdfParser._is_not_inside_table(obj_outside_left, table_bboxes) is True

    # 2. Object inside
    obj_inside = {
        "object_type": "char",
        "x0": 120,
        "top": 120,
        "x1": 130,
        "bottom": 130,
    }
    assert PdfParser._is_not_inside_table(obj_inside, table_bboxes) is False

    # 3. Object partially overlapping (intersects)
    obj_intersect = {
        "object_type": "char",
        "x0": 90,
        "top": 90,
        "x1": 110,
        "bottom": 110,
    }
    assert PdfParser._is_not_inside_table(obj_intersect, table_bboxes) is False

    # 4. Object outside (below)
    obj_outside_below = {
        "object_type": "char",
        "x0": 150,
        "top": 210,
        "x1": 160,
        "bottom": 220,
    }
    assert PdfParser._is_not_inside_table(obj_outside_below, table_bboxes) is True

    # 5. Non-char object (should always be True)
    obj_non_char = {
        "object_type": "rect",
        "x0": 120,
        "top": 120,
        "x1": 130,
        "bottom": 130,
    }
    assert PdfParser._is_not_inside_table(obj_non_char, table_bboxes) is True


def test_is_not_inside_table_multiple_bboxes():
    table_bboxes = [(10, 10, 20, 20), (50, 50, 60, 60)]

    # Object between tables
    obj = {"object_type": "char", "x0": 30, "top": 30, "x1": 40, "bottom": 40}
    assert PdfParser._is_not_inside_table(obj, table_bboxes) is True

    # Object in first table
    obj_in_1 = {"object_type": "char", "x0": 12, "top": 12, "x1": 15, "bottom": 15}
    assert PdfParser._is_not_inside_table(obj_in_1, table_bboxes) is False

    # Object in second table
    obj_in_2 = {"object_type": "char", "x0": 52, "top": 52, "x1": 55, "bottom": 55}
    assert PdfParser._is_not_inside_table(obj_in_2, table_bboxes) is False
