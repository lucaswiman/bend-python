import Base

# Specification of the C driver's thread-state protocol, not verification of C.
# Attached means a valid CPython thread state (and the GIL on GIL builds).
# Free-threaded Python still requires attachment; it has no GIL to reacquire.
type State is Data:
  Attached{}
  Detached{}

# Native runtime health: ordinary Python exceptions do not mark a context failed.
type Outcome is Data:
  Succeeded{}
  Failed{}

type Operation is Data:
  Save{}
  AcquireLease{}
  ReturnLease{outcome: Outcome, cache_space: Bool}
  NativeWork{outcome: Outcome}
  Restore{}
  PythonEffect{}

# A Python effect includes reference-count changes and exception operations.
# NativeWork is stipulated to contain none; establishing that for C is trusted.
def step(state: State, operation: Operation) -> Maybe<State>:
  match state operation:
    case Attached{} Save{}:
      Some{Detached{}}
    case Detached{} Restore{}:
      Some{Attached{}}
    case Attached{} PythonEffect{}:
      Some{Attached{}}
    case Detached{} AcquireLease{}:
      Some{Detached{}}
    case Detached{} ReturnLease{_, _}:
      Some{Detached{}}
    case current NativeWork{_}:
      Some{current}
    case _ _:
      None{}

def run(operations: List<Operation>, state: State) -> Maybe<State>:
  match operations:
    case []:
      Some{state}
    case operation <> tail:
      Maybe.bind(&1, State, State, step(state, operation), next => run(tail, next))

# One invocation owns its context throughout native work and attached effects.
# Only cache acquire/return takes a shared lock; evaluation has no shared mutex.
def acquire_call() -> List<Operation>:
  [Save{}, AcquireLease{}, Restore{}]

def return_call(outcome: Outcome, cache_space: Bool) -> List<Operation>:
  [Save{}, ReturnLease{outcome, cache_space}, Restore{}]

def native_call(release_gil: Bool, outcome: Outcome) -> List<Operation>:
  match release_gil:
    case True{}:
      [Save{}, NativeWork{outcome}, Restore{}]
    case False{}:
      [NativeWork{outcome}]

# Each slot denotes one distinct runtime allocation. The C cache must preserve
# that identity and perform acquisition/return atomically; these are trust inputs.
type Context is Data:
  Idle{}
  Leased{}
  Discarded{}

def eligible(context: Maybe<&2, Context>) -> Bool:
  match context:
    case Some{Idle{}}:
      True{}
    case _:
      False{}

def available(pool: List<&2, Context>, slot: Nat) -> Bool:
  eligible(List.get(&2, Context, pool, slot))

def reserve(pool: List<&2, Context>, slot: Nat) -> List<&2, Context>:
  List.set(&2, Context, pool, slot, Leased{})

def acquire_if(allowed: Bool, pool: List<&2, Context>, slot: Nat) -> Maybe<List<&2, Context>>:
  match allowed:
    case True{}:
      Some{reserve(pool, slot)}
    case False{}:
      None{}

def acquire(+pool: List<&2, Context>, +slot: Nat) -> Maybe<List<&2, Context>>:
  acquire_if(available(pool, slot), pool, slot)

def completion(outcome: Outcome, cache_space: Bool) -> Context:
  match outcome cache_space:
    case Succeeded{} True{}:
      Idle{}
    case _ _:
      Discarded{}

def finish(pool: List<&2, Context>, slot: Nat, outcome: Outcome, cache_space: Bool) -> List<&2, Context>:
  List.set(&2, Context, pool, slot, completion(outcome, cache_space))
