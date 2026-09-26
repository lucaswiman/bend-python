import Base
import ./bend/python.bend as Python
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

def attribute_values(values: Pair(Python.Object, Python.Object)) -> IO(Python.Object):
  (value, name) = values
  do IO<Python.Object>:
    text : String <- Python.to_string(name)
    Python.getattr(value, text)

def attribute(request: Python.Call) -> IO(Python.Object):
  do IO<Python.Object>:
    values : Pair(Python.Object, Python.Object) <- Python.binary(request)
    attribute_values(values)

def length_value(value: Python.Object) -> IO(Python.Object):
  do IO<Python.Object>:
    n : Nat <- Python.len(value)
    Python.from_nat(n)

def length(request: Python.Call) -> IO(Python.Object):
  do IO<Python.Object>:
    value : Python.Object <- Python.unary(request)
    length_value(value)

def make_none(request: Python.Call) -> IO(Python.Object):
  match request:
    case Python.PyCall{[], keywords}:
      do IO<Python.Object>:
        Python.require_no_kwargs(keywords)
        Python.none()
    case _:
      Python.type_error(Python.Object, "expected no arguments")

def entries(args: List<Python.Object>) -> IO(List<Pair(Python.Object, Python.Object)>):
  match args:
    case []:
      IO.pure(List<Pair(Python.Object, Python.Object)>, [])
    case [_]:
      Python.type_error(List<Pair(Python.Object, Python.Object)>, "expected alternating keys and values")
    case key <> value <> tail:
      do IO<List<Pair(Python.Object, Python.Object)>>:
        rest : List<Pair(Python.Object, Python.Object)> <- entries(tail)
        return (key, value) <> rest

def make_dict(request: Python.Call) -> IO(Python.Object):
  match request:
    case Python.PyCall{args, keywords}:
      do IO<Python.Object>:
        Python.require_no_kwargs(keywords)
        items : List<Pair(Python.Object, Python.Object)> <- entries(args)
        Python.dict(items)

def invoke(request: Python.Call) -> IO(Python.Object):
  match request:
    case Python.PyCall{function <> args, keywords}:
      do IO<Python.Object>:
        Python.require_no_kwargs(keywords)
        Python.invoke(function, args)
    case _:
      Python.type_error(Python.Object, "expected a callable as the first argument")

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
    Python.export("attribute", attribute, False{})
    Python.export("length", length, False{})
    Python.export("make_none", make_none, False{})
    Python.export("make_dict", make_dict, False{})
    Python.export("invoke", invoke, False{})
