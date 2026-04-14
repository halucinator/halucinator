from __future__ import annotations


class HalSymbolConfig:
    '''
        Description of a symbol for halucinators config
    '''
    def __init__(self, config_file: str, name: str, addr: int, size: int = 0) -> None:
        self.config_file: str = config_file
        self.name: str = name
        self.addr: int = addr
        self.size: int = size

    def is_valid(self) -> bool:
        '''
            Used to check if symbol entry is valid (Always true)
        '''
        return True

    def __repr__(self) -> str:
        return "SymConfig(%s){%s, %s(%i),%i}" % \
                (self.config_file, self.name, hex(self.addr), self.addr, self.size)
