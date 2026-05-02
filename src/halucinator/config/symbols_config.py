class HalSymbolConfig(object):
    '''
        Description of a symbol for halucinators config
    '''
    def __init__(self, config_file, name, addr, size = 0):
        self.config_file = config_file
        self.name = name
        self.addr = addr
        self.size = size

    def is_valid(self):
        '''
            Used to check if symbol entry is valid (Always true) 
        '''
        return True

    def __repr__(self):
        return "SymConfig(%s){%s, %s(%i),%i}" % \
                (self.config_file, self.name, hex(self.addr), self.addr, self.size)
