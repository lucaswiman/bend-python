import Base

# A borrowed Python object, scoped to one exported call. The C shim checks every
# handle before use. Never store a handle for use by a later Python call.
type Object is Data:
  PyObject{id: U32}

type Call is Type:
  PyCall{args: List<Object>, kwargs: Object}

# The low-level API preserves Python objects, including identity and cycles.
# Native calls hold an invocation arena; these effects run with Python attached.
def export(name: String, function: Call -> IO(Object), release_gil: Bool) -> IO(Unit):
  import "./python.c"

def require_no_kwargs(kwargs: Object) -> IO(Unit):
  import "./python.c"

def to_u32(value: Object) -> IO(U32):
  import "./python.c"

def from_u32(value: U32) -> IO(Object):
  import "./python.c"

def to_f32(value: Object) -> IO(F32):
  import "./python.c"

def from_f32(value: F32) -> IO(Object):
  import "./python.c"

def to_string(value: Object) -> IO(String):
  import "./python.c"

def from_string(value: String) -> IO(Object):
  import "./python.c"

def to_bool(value: Object) -> IO(Bool):
  import "./python.c"

def from_bool(value: Bool) -> IO(Object):
  import "./python.c"

def get_item(value: Object, key: Object) -> IO(Object):
  import "./python.c"

def set_item(value: Object, key: Object, item: Object) -> IO(Unit):
  import "./python.c"

def getattr(value: Object, name: String) -> IO(Object):
  import "./python.c"

def call(function: Object, args: Object, kwargs: Object) -> IO(Object):
  import "./python.c"

def builtins(name: String) -> IO(Object):
  import "./python.c"

def none() -> IO(Object):
  import "./python.c"

def empty_dict() -> IO(Object):
  import "./python.c"

def tuple(values: List<Object>) -> IO(Object):
  import "./python.c"

def list(values: List<Object>) -> IO(Object):
  import "./python.c"

def len(value: Object) -> IO(Nat):
  import "./python.c"

# Exact, unlike U32.from_nat, which wraps lengths and counts past 2^32.
def from_nat(value: Nat) -> IO(Object):
  import "./python.c"

def type_error(-A: Type, message: String) -> IO(A):
  import "./python.c"

# Python truthiness (bool(value)), including __bool__ and __len__.
def truthy(value: Object) -> IO(Bool):
  import "./python.c"

# Bytes of any C-contiguous buffer (bytes, bytearray, memoryview, ...), each a
# U32 in range(256).
def to_bytes(value: Object) -> IO(List<U32>):
  import "./python.c"

# A bytes object; a value outside range(256) raises ValueError.
def from_bytes(values: List<U32>) -> IO(Object):
  import "./python.c"

# importlib.import_module(name), e.g. "operator" or "collections.abc".
def import_module(name: String) -> IO(Object):
  import "./python.c"

# Common Python operations are assembled here from the small effect boundary.
def invoke(function: Object, args: List<Object>) -> IO(Object):
  do IO<Object>:
    positional : Object <- tuple(args)
    keywords : Object <- empty_dict()
    call(function, positional, keywords)

def construct(name: String, args: List<Object>) -> IO(Object):
  do IO<Object>:
    function : Object <- builtins(name)
    invoke(function, args)

def fill_dict(entries: List<Pair(Object, Object)>, +target: Object) -> IO(Object):
  match entries:
    case []:
      IO.pure(Object, target)
    case (key, value) <> tail:
      do IO<Object>:
        set_item(target, key, value)
        fill_dict(tail, target)

def dict(entries: List<Pair(Object, Object)>) -> IO(Object):
  do IO<Object>:
    target : Object <- empty_dict()
    fill_dict(entries, target)

# Total argument-list helpers remain pure and can be used in checked laws.
def arity(args: List<Object>) -> Nat:
  match args:
    case []:
      0n
    case _ <> tail:
      1n+arity(tail)

def argument(args: List<Object>, index: Nat) -> Maybe<Object>:
  match args index:
    case [] _:
      None{}
    case value <> _ 0n:
      Some{value}
    case _ <> tail 1n+pred:
      argument(tail, pred)

def singleton(args: List<Object>) -> Maybe<Object>:
  match args:
    case [value]:
      Some{value}
    case _:
      None{}

def pair(args: List<Object>) -> Maybe<Pair(Object, Object)>:
  match args:
    case [left, right]:
      Some{(left, right)}
    case _:
      None{}

# Checked extraction is shared by the typed adapters; its pure singleton/pair
# branches are the ones specified by LAWS.bend and discharged in PROOF.bend.
def require_singleton(value: Maybe<Object>) -> IO(Object):
  match value:
    case Some{item}:
      IO.pure(Object, item)
    case None{}:
      type_error(Object, "expected exactly one positional argument")

def require_pair(value: Maybe<Pair(Object, Object)>) -> IO(Pair(Object, Object)):
  match value:
    case Some{items}:
      IO.pure(Pair(Object, Object), items)
    case None{}:
      type_error(Pair(Object, Object), "expected exactly two positional arguments")

def unary(request: Call) -> IO(Object):
  match request:
    case PyCall{args, kwargs}:
      do IO<Object>:
        require_no_kwargs(kwargs)
        require_singleton(singleton(args))

def binary(request: Call) -> IO(Pair(Object, Object)):
  match request:
    case PyCall{args, kwargs}:
      do IO<Pair(Object, Object)>:
        require_no_kwargs(kwargs)
        require_pair(pair(args))

# Template arguments are specialized at compile time, leaving a captureless
# callback that may safely be invoked repeatedly by the native runtime.
def invoke_unary(~A: Type, ~decode: Object -> IO(A), ~encode: A -> IO(Object),
  ~f: A -> A, request: Call) -> IO(Object):
  do IO<Object>:
    value : Object <- unary(request)
    x : A <- decode(value)
    encode(f(x))

def invoke_binary_u32_values(~f: U32 -> U32 -> U32, values: Pair(Object, Object)) -> IO(Object):
  (left, right) = values
  do IO<Object>:
    x : U32 <- to_u32(left)
    y : U32 <- to_u32(right)
    from_u32(f(x, y))

def invoke_binary_u32(~f: U32 -> U32 -> U32, request: Call) -> IO(Object):
  do IO<Object>:
    values : Pair(Object, Object) <- binary(request)
    invoke_binary_u32_values(~f, values)

def export_u32(~f: U32 -> U32, name: String, release_gil: Bool) -> IO(Unit):
  export(name, invoke_unary(~U32, ~to_u32, ~from_u32, ~f), release_gil)

def export_f32(~f: F32 -> F32, name: String, release_gil: Bool) -> IO(Unit):
  export(name, invoke_unary(~F32, ~to_f32, ~from_f32, ~f), release_gil)

def export_string(~f: String -> String, name: String, release_gil: Bool) -> IO(Unit):
  export(name, invoke_unary(~String, ~to_string, ~from_string, ~f), release_gil)

def export_bool(~f: Bool -> Bool, name: String, release_gil: Bool) -> IO(Unit):
  export(name, invoke_unary(~Bool, ~to_bool, ~from_bool, ~f), release_gil)

def export_binary_u32(~f: U32 -> U32 -> U32, name: String, release_gil: Bool) -> IO(Unit):
  export(name, invoke_binary_u32(~f), release_gil)

# A sealed borrow of existing native float32 CPU storage. Unlike Object, the
# access capability is affine. Python aliases must remain synchronized.
type F32View is Type:
  F32View{id: U32}

def borrow_f32(value: Object, writable: Bool) -> IO(F32View):
  import "./python.c"

def f32_size(view: F32View) -> IO(Pair(F32View, Nat)):
  import "./python.c"

def f32_shape(view: F32View) -> IO(Pair(F32View, List<Nat>)):
  import "./python.c"

# Flat logical row-major indices, with bounds checks rather than wrapping.
def f32_read(view: F32View, index: Nat) -> IO(Pair(F32View, F32)):
  import "./python.c"

def f32_write(view: F32View, index: Nat, value: F32) -> IO(F32View):
  import "./python.c"

# Fuse one load, a captureless Bend callback, and one store.
def f32_modify(view: F32View, index: Nat, function: F32 -> F32) -> IO(F32View):
  import "./python.c"

def f32_release(view: F32View) -> IO(Unit):
  import "./python.c"
