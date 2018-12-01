#!/usr/bin/python3

from collections import OrderedDict
import sys

from lib.components.eth import web3, TransactionReceipt

class _ContractBase:

    def __init__(self, name, abi):
        self.abi = abi
        names = [i['name'] for i in abi if i['type']=="function"]
        duplicates = set(i for i in names if names.count(i)>1)
        if duplicates:
            raise ValueError("Ambiguous contract functions in {}: {}".format(
                name, ",".join(duplicates)))
        self._name = name
        self.topics = dict((
            i['name'], 
            web3.sha3(text="{}({})".format(i['name'],
                ",".join(x['type'] for x in i['inputs']))
                ).hex()
        ) for i in abi if i['type']=="event")
        self.signatures = dict((
            i['name'],
            web3.sha3(text="{}({})".format(i['name'],
                ",".join(x['type'] for x in i['inputs'])
                )).hex()[:10]
        ) for i in abi if i['type']=="function")
            
            


class ContractDeployer(_ContractBase):

    def __init__(self, name, interface):
        self.tx = None
        self.bytecode = interface['bin']
        self._deployed = OrderedDict()
        super().__init__(name, interface['abi'])
    
    def __iter__(self):
        return iter(self._deployed.values())

    def __getitem__(self, i):
        return list(self._deployed.values())[i]

    def __len__(self):
        return len(self._deployed)

    def __repr__(self):
        return "<{} ContractDeployer object>".format(self._name)

    def list(self):
        return list(self._deployed)

    def deploy(self, account, *args):
        contract = web3.eth.contract(abi = self.abi, bytecode = self.bytecode)
        types = [i['type'] for i in next(i for i in self.abi if i['type']=="constructor")['inputs']]
        args = _format_inputs("constructor", args, types)
        tx = account._contract_tx(contract.constructor, args, {})
        deployed = self.at(tx.contractAddress, account)
        deployed.tx = tx
        return deployed
    
    def at(self, address, owner = None):
        address = web3.toChecksumAddress(address)
        if address in self._deployed:
            return self._deployed[address]
        self._deployed[address] = Contract(address, self._name, self.abi, owner)
        return self._deployed[address]


class Contract(str,_ContractBase):

    def __new__(cls, address, *args):
        return super().__new__(cls, address)

    def __init__(self, address, name, abi, owner):
        super().__init__(name, abi)
        self._contract = web3.eth.contract(address = address, abi = abi)
        for i in [i for i in abi if i['type']=="function"]:
            fn = getattr(self._contract.functions,i['name'])
            if i['stateMutability'] in ('view','pure'):
                setattr(self, i['name'], ContractCall(fn, i, owner))
            else:
                setattr(self, i['name'], ContractTx(fn, i, owner))
        self.owner = owner
    
    def __repr__(self):
        return "<{0._name} Contract object '{0.address}'>".format(self)

    def __str__(self):
        return self.__repr__()

    def __getattr__(self, name):
        return getattr(self._contract, name)

    def balance(self):
        return web3.eth.getBalance(self._contract.address)

class _ContractMethod:

    def __init__(self, fn, abi, owner):
        self._fn = fn
        self.abi = abi
        self._owner = owner
        self.sig = web3.sha3(text="{}({})".format(
            abi['name'],
            ",".join(i['type'] for i in abi['inputs'])
            )).hex()[:10]

    def __repr__(self):
        return "<{} object '{}({})'>".format(
            type(self).__name__,
            self.abi['name'],
            ",".join(i['type'] for i in self.abi['inputs']))
    
    def _format_inputs(self, args):
        types = [i['type'] for i in self.abi['inputs']]
        return _format_inputs(self.abi['name'], args, types)

class ContractTx(_ContractMethod):

    def __call__(self, *args):
        if args and type(args[-1]) is dict:
            args, tx = (args[:-1], args[-1])
            if 'from' not in tx:
                tx['from'] = self._owner
            if 'value' in tx and type(tx['value']) is float:
                tx['value'] = int(tx['value'])
        else:
            tx = {'from': self._owner}
        return tx['from']._contract_tx(self._fn, self._format_inputs(args), tx)

class ContractCall(_ContractMethod):

    def __call__(self, *args):
        result = self._fn(*self._format_inputs(args)).call()
        if type(result) is not list:
            return web3.toHex(result) if type(result) is bytes else result
        return [(web3.toHex(i) if type(i) is bytes else i) for i in result]


def _format_inputs(name, inputs, types):
        inputs = list(inputs)
        if len(inputs) != len(types):
            raise AttributeError(
                "{} requires the following arguments: {}".format(
                name,",".join(types)))
        for i, type_ in enumerate(types):
            if type_[-1]=="]":
                t,length = type_.rstrip(']').split('[')
                if length!="" and len(inputs[i])!=int(length):
                    raise ValueError(
                        "'{}': Argument {}, sequence has a length of {}, should be {}".format(
                            name, i, len(inputs[i]), type_))
                inputs[i] = _format_inputs(name, inputs[i],[t]*len(inputs[i]))
                continue
            try:
                elif "int" in type_:
                    inputs[i]=int(inputs[i])
                elif "bytes" in type_ and type(inputs[i]) is not bytes:
                    if type(inputs[i]) is not str:
                        inputs[i]=int(inputs[i]).to_bytes(int(type_[5:]),"big")
                    elif inputs[i][:2]!="0x":
                        inputs[i]=inputs[i].encode() 
            except:
                raise ValueError(
                    "'{}': Argument {}, could not convert {} '{}' to type {}".format(
                        name,i,type(inputs[i]).__name__,inputs[i],type_))
        return inputs