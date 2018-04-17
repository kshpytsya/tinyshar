import tinyshar


def test_empty():
    shar = tinyshar.SharCreator()
    b''.join(shar.render())


def test_one():
    shar = tinyshar.SharCreator()

    shar.add_file("one", b"abc" * 10)

    b''.join(shar.render())
