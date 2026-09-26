import Base
import ../bend/python.bend as Python
import ./arithmetic.bend as Arithmetic

def echo(request: Python.Call) -> IO(Python.Object):
  Python.unary(request)

def kwargs(request: Python.Call) -> IO(Python.Object):
  match request:
    case Python.PyCall{[], keywords}:
      IO.pure(Python.Object, keywords)
    case _:
      Python.type_error(Python.Object, "expected no positional arguments")

def pack(request: Python.Call) -> IO(Python.Object):
  match request:
    case Python.PyCall{args, keywords}:
      do IO<Python.Object>:
        Python.require_no_kwargs(keywords)
        Python.tuple(args)

def pack_list(request: Python.Call) -> IO(Python.Object):
  match request:
    case Python.PyCall{args, keywords}:
      do IO<Python.Object>:
        Python.require_no_kwargs(keywords)
        Python.list(args)

def getitem_values(values: Pair(Python.Object, Python.Object)) -> IO(Python.Object):
  (value, key) = values
  Python.get_item(value, key)

def getitem(request: Python.Call) -> IO(Python.Object):
  do IO<Python.Object>:
    values : Pair(Python.Object, Python.Object) <- Python.binary(request)
    getitem_values(values)

def setitem(request: Python.Call) -> IO(Python.Object):
  match request:
    case Python.PyCall{[+value, key, item], keywords}:
      do IO<Python.Object>:
        Python.require_no_kwargs(keywords)
        Python.set_item(value, key, item)
        return value
    case _:
      Python.type_error(Python.Object, "expected exactly three positional arguments")

def call(request: Python.Call) -> IO(Python.Object):
  match request:
    case Python.PyCall{function <> args, keywords}:
      do IO<Python.Object>:
        positional : Python.Object <- Python.tuple(args)
        Python.call(function, positional, keywords)
    case _:
      Python.type_error(Python.Object, "expected a callable as the first argument")

def construct(request: Python.Call) -> IO(Python.Object):
  match request:
    case Python.PyCall{name <> args, keywords}:
      do IO<Python.Object>:
        Python.require_no_kwargs(keywords)
        builtin_name : String <- Python.to_string(name)
        Python.construct(builtin_name, args)
    case _:
      Python.type_error(Python.Object, "expected a builtin name as the first argument")

def main() -> IO(Unit):
  do IO<Unit>:
    Python.export_u32(~(x => Arithmetic.square(x)), "square", False{})
    Python.export_u32(~Arithmetic.increment, "increment", False{})
    Python.export_u32(~Arithmetic.identity, "identity", False{})
    Python.export_binary_u32(~Arithmetic.add, "add", False{})
    Python.export("echo", echo, False{})
    Python.export_string(~(x => x), "echo_string", False{})
    Python.export_bool(~Arithmetic.flip, "flip", False{})
    Python.export_f32(~Arithmetic.half, "half", False{})
    Python.export("kwargs", kwargs, False{})
    Python.export("pack", pack, False{})
    Python.export("pack_list", pack_list, False{})
    Python.export("getitem", getitem, False{})
    Python.export("setitem", setitem, False{})
    Python.export("call", call, False{})
    Python.export("construct", construct, False{})
    Python.export_u32(~Arithmetic.slow, "slow", False{})
    Python.export_u32(~Arithmetic.slow, "slow_release", True{})
