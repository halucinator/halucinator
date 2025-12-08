"""
Test the BPStruct Class
"""

from halucinator.bp_handlers.bp_handler import BPStruct


class TestStruct1(BPStruct):
    """
    Test structure to ensure formating class object works
    """

    FORMAT = {
        "test_le_U32": "<I",
        "test_be_i16": ">h",
        "test_name": "10s",
    }


if __name__ == "__main__":
    test_struct = BPStruct(le_U32="<I", be_i16=">h", name="10s")
    print(test_struct)
    len(test_struct)
    buffer = test_struct.build_buffer()
    print(f"Lengths Equal? {len(buffer)} {len(test_struct)}")
    print(f"Build buffer = {buffer}")
    test_struct.le_U32 = 0x12345678
    test_struct.be_i16 = 0x7890
    test_struct.name = b"My Name"

    buffer = test_struct.build_buffer()
    print(f"Lengths Equal? {len(buffer)} {len(test_struct)}")
    print(f"Built After Write = {buffer}")
    print(test_struct)

    test_struct.parse_buffer(buffer)
    print(f"Lengths Equal? {len(buffer)} {len(test_struct)}")
    print(f"Built Read = {buffer}")
    print(test_struct)

    # Should increase the interger values by 1 and change name to new name
    NEW_BUFF = b"yV4\x12x\x91New Name\x00\x00"
    test_struct.parse_buffer(NEW_BUFF)
    print(f"After Modified Read = {NEW_BUFF}")
    print(test_struct)

    # Make sure fields get set properly
    print(TestStruct1())
