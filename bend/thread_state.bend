import Base

# Specification of the C driver's thread-state protocol, not verification of C.
# Attached means a valid CPython thread state (and the GIL on GIL builds).
# Free-threaded Python still requires attachment; it has no GIL to reacquire.
type State is Data:
  Attached{}
  Detached{}

type Outcome is Data:
  Succeeded{}
  Failed{}
  Poisoned{}

type Operation is Data:
  Save{}
  WaitRuntime{}
  NativeWork{outcome: Outcome}
  UnlockRuntime{}
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
    case Detached{} WaitRuntime{}:
      Some{Detached{}}
    case current NativeWork{_}:
      Some{current}
    case current UnlockRuntime{}:
      Some{current}
    case _ _:
      None{}

def run(operations: List<Operation>, state: State) -> Maybe<State>:
  match operations:
    case []:
      Some{state}
    case operation <> tail:
      Maybe.bind(&1, State, State, step(state, operation), next => run(tail, next))

# bp_native detaches before waiting for its mutex. Both normal and error exits
# pass through unlock and restore; retain-GIL exports restore before native work.
def native_call(release_gil: Bool, outcome: Outcome) -> List<Operation>:
  match release_gil:
    case True{}:
      [Save{}, WaitRuntime{}, NativeWork{outcome}, UnlockRuntime{}, Restore{}]
    case False{}:
      [Save{}, WaitRuntime{}, Restore{}, NativeWork{outcome}, UnlockRuntime{}]
