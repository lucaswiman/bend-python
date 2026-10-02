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

law cursor_initial:
  for +length: Nat
  {State.position(length, length) == 0n : Nat}

law cursor_terminal:
  for length: Nat
  {State.position(length, 0n) == length : Nat}

law cursor_conservation:
  for +length: Nat
  for +remaining: Nat
  for bounded: {Nat.is_le(remaining, length) == True{} : Bool}
  {Nat.add(State.position(length, remaining), remaining) == length : Nat}

law cursor_in_bounds:
  for +length: Nat
  for +pred: Nat
  for bounded: {Nat.is_le(1n+pred, length) == True{} : Bool}
  {Nat.is_lt(State.position(length, 1n+pred), length) == True{} : Bool}

law cursor_advance:
  for +length: Nat
  for +pred: Nat
  for bounded: {Nat.is_le(1n+pred, length) == True{} : Bool}
  {State.position(length, State.remaining_after_step(1n+pred)) == 1n+State.position(length, 1n+pred) : Nat}

law cursor_decrease:
  for +length: Nat
  for +pred: Nat
  for bounded: {Nat.is_le(1n+pred, length) == True{} : Bool}
  {Nat.is_le(State.remaining_after_step(1n+pred), length) == True{} : Bool}

law map_zero:
  for memory: List<F32>
  {State.apply_steps(0n, memory, Unit{}) == memory : List<F32>}

law map_step:
  for f: F32 -> F32
  for count: Nat
  for value: F32
  {Tensor.map_steps(~f, 1n+count)(value) == (f(value), Tensor.map_steps(~f, count)) : Pair(F32, Python.F32MapSteps(count))}

law map_count:
  for count: Nat
  for steps: Python.F32MapSteps(count)
  {State.step_count(count, steps) == count : Nat}

law map_elements:
  for f: F32 -> F32
  for memory: List<F32>
  {State.apply_steps(List.length(&1, F32, memory), memory, Tensor.map_steps(~f, List.length(&1, F32, memory))) == List.map(~F32, ~F32, ~f, memory) : List<F32>}
