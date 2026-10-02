import Base
import ./tensor_state.bend as State
import ./tensor.bend as Tensor
import ./python.bend as Python

law replace_preserves_count:
  for memory: List<&2, F32>
  for index: Nat
  for value: F32
  {List.length(&2, F32, State.replace(memory, index, value)) == List.length(&2, F32, memory) : Nat}

law read_after_write:
  for memory: List<&2, F32>
  for index: Nat
  for value: F32
  for old: F32
  for present: {State.read(memory, index) == Some{old} : Maybe<&2, F32>}
  {State.read(State.replace(memory, index, value), index) == Some{value} : Maybe<&2, F32>}

law other_cells_unchanged:
  for memory: List<&2, F32>
  for written: Nat
  for observed: Nat
  for value: F32
  for different: {Nat.is_eq(written, observed) == False{} : Bool}
  {State.read(State.replace(memory, written, value), observed) == State.read(memory, observed) : Maybe<&2, F32>}

law read_only_rejected:
  for memory: List<&2, F32>
  for index: Nat
  for value: F32
  {State.write(State.Live{False{}}, memory, index, value) == None{} : Maybe<List<&2, F32>>}

law released_write_rejected:
  for memory: List<&2, F32>
  for index: Nat
  for value: F32
  {State.write(State.Released{}, memory, index, value) == None{} : Maybe<List<&2, F32>>}

law out_of_bounds_rejected:
  for memory: List<&2, F32>
  for index: Nat
  for value: F32
  for outside: {Nat.is_lt(index, List.length(&2, F32, memory)) == False{} : Bool}
  {State.write(State.Live{True{}}, memory, index, value) == None{} : Maybe<List<&2, F32>>}

law writable_index_accepted:
  for memory: List<&2, F32>
  for index: Nat
  for value: F32
  for inside: {Nat.is_lt(index, List.length(&2, F32, memory)) == True{} : Bool}
  {State.write(State.Live{True{}}, memory, index, value) == Some{State.replace(memory, index, value)} : Maybe<List<&2, F32>>}

law close_live:
  for writable: Bool
  {State.close(State.Live{writable}) == Some{State.Released{}} : Maybe<&2, State.Lease>}

law double_close_rejected:
  {State.close(State.Released{}) == None{} : Maybe<&2, State.Lease>}

law map_zero:
  for f: F32 -> F32
  for view: Python.F32View
  for index: Nat
  {Tensor.map_loop(~f, 0n, view, index) == IO.pure(Python.F32View, view) : IO(Python.F32View)}

law map_step:
  for f: F32 -> F32
  for count: Nat
  for view: Python.F32View
  for index: Nat
  {Tensor.map_loop(~f, 1n+count, view, index) == IO.bind(Python.F32View, Python.F32View, Python.f32_modify(view, index, f), next => Tensor.map_loop(~f, count, next, Nat.add(index, 1n))) : IO(Python.F32View)}
