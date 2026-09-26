import Base

# Specification of the C driver's thread-state and lease protocol, not
# verification of C. Attached means a valid CPython thread state (and the GIL
# on GIL builds). Free-threaded Python still requires attachment.
#
# The native states cover bp_native between setting and clearing bp_current:
# Bend evaluation, where the C bridge makes no Python API call. A native
# failure longjmps back inside that window, so it also leaves via Leave.
type State is Data:
  Attached{}
  Detached{}
  AttachedNative{}
  DetachedNative{}

# Native runtime health: ordinary Python exceptions do not mark a context failed.
type Outcome is Data:
  Succeeded{}
  Failed{}

# A Python effect includes reference-count changes and exception operations.
type Operation is Data:
  Save{}
  Restore{}
  Enter{}
  Leave{outcome: Outcome}
  PythonEffect{}
  AcquireLease{slot: Nat}
  ReturnLease{slot: Nat, outcome: Outcome, small_heap: Bool, cache_space: Bool}

def is_native(state: State) -> Bool:
  match state:
    case AttachedNative{}:
      True{}
    case DetachedNative{}:
      True{}
    case _:
      False{}

def is_leave(operation: Operation) -> Bool:
  match operation:
    case Leave{_}:
      True{}
    case _:
      False{}

# Attachment and native transitions. Only step_session handles lease operations.
def step(state: State, operation: Operation) -> Maybe<State>:
  match state operation:
    case Attached{} Save{}:
      Some{Detached{}}
    case Detached{} Restore{}:
      Some{Attached{}}
    case Attached{} PythonEffect{}:
      Some{Attached{}}
    case Attached{} Enter{}:
      Some{AttachedNative{}}
    case Detached{} Enter{}:
      Some{DetachedNative{}}
    case AttachedNative{} Leave{_}:
      Some{Attached{}}
    case DetachedNative{} Leave{_}:
      Some{Detached{}}
    case _ _:
      None{}

def run(operations: List<Operation>, state: State) -> Maybe<State>:
  match operations:
    case []:
      Some{state}
    case operation <> tail:
      Maybe.bind(&1, State, State, step(state, operation), next => run(tail, next))

# C detaches during native work when the export asks to, and always on
# free-threaded builds (Py_GIL_DISABLED), where staying attached only stalls
# stop-the-world pauses.
def detaches(release_gil: Bool, free_threaded: Bool) -> Bool:
  release_gil || free_threaded

def native_call(detach: Bool, outcome: Outcome) -> List<Operation>:
  match detach:
    case True{}:
      [Save{}, Enter{}, Leave{outcome}, Restore{}]
    case False{}:
      [Enter{}, Leave{outcome}]

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

def leased(context: Maybe<&2, Context>) -> Bool:
  match context:
    case Some{Leased{}}:
      True{}
    case _:
      False{}

def returnable(pool: List<&2, Context>, slot: Nat) -> Bool:
  leased(List.get(&2, Context, pool, slot))

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

# C's bp_runtime_release: an instance is reusable when it is not poisoned (no
# native failure) and its heap stayed within 32 MiB; it is cached when it is
# reusable and fewer than eight instances are idle. Otherwise it is unmapped.
def completion(outcome: Outcome, small_heap: Bool, cache_space: Bool) -> Context:
  match outcome small_heap cache_space:
    case Succeeded{} True{} True{}:
      Idle{}
    case _ _ _:
      Discarded{}

def finish(pool: List<&2, Context>, slot: Nat, outcome: Outcome, small_heap: Bool, cache_space: Bool) -> List<&2, Context>:
  List.set(&2, Context, pool, slot, completion(outcome, small_heap, cache_space))

def finish_if(allowed: Bool, pool: List<&2, Context>, slot: Nat, outcome: Outcome, small_heap: Bool, cache_space: Bool) -> Maybe<List<&2, Context>>:
  match allowed:
    case True{}:
      Some{finish(pool, slot, outcome, small_heap, cache_space)}
    case False{}:
      None{}

# Only a leased slot may be returned; a double return is rejected.
def release(+pool: List<&2, Context>, +slot: Nat, outcome: Outcome, small_heap: Bool, cache_space: Bool) -> Maybe<List<&2, Context>>:
  finish_if(returnable(pool, slot), pool, slot, outcome, small_heap, cache_space)

# The combined model: one invocation's thread state together with the pool.
type Session is Data:
  Session{state: State, pool: List<&2, Context>}

def detached_with(pool: Maybe<List<&2, Context>>) -> Maybe<Session>:
  match pool:
    case Some{next}:
      Some{Session{Detached{}, next}}
    case None{}:
      None{}

def with_pool(state: Maybe<State>, pool: List<&2, Context>) -> Maybe<Session>:
  match state:
    case Some{next}:
      Some{Session{next, pool}}
    case None{}:
      None{}

# The pool mutex is taken only while detached; lease operations in any other
# state are rejected.
def step_session(session: Session, operation: Operation) -> Maybe<Session>:
  match session operation:
    case Session{Detached{}, pool} AcquireLease{slot}:
      detached_with(acquire(pool, slot))
    case Session{Detached{}, pool} ReturnLease{slot, outcome, small_heap, cache_space}:
      detached_with(release(pool, slot, outcome, small_heap, cache_space))
    case Session{_, _} AcquireLease{_}:
      None{}
    case Session{_, _} ReturnLease{_, _, _, _}:
      None{}
    case Session{state, pool} operation:
      with_pool(step(state, operation), pool)

def run_session(operations: List<Operation>, session: Session) -> Maybe<Session>:
  match operations:
    case []:
      Some{session}
    case operation <> tail:
      Maybe.bind(&1, Session, Session, step_session(session, operation), next => run_session(tail, next))

# One invocation owns its context throughout native work and attached effects.
# Only cache acquire/return takes a shared lock; evaluation has no shared mutex.
def acquire_call(slot: Nat) -> List<Operation>:
  [Save{}, AcquireLease{slot}, Restore{}]

def return_call(slot: Nat, outcome: Outcome, small_heap: Bool, cache_space: Bool) -> List<Operation>:
  [Save{}, ReturnLease{slot, outcome, small_heap, cache_space}, Restore{}]

# A lease is healthy only if every native step succeeded; one failure poisons it.
def health(outcomes: List<&2, Outcome>) -> Outcome:
  match outcomes:
    case []:
      Succeeded{}
    case Succeeded{} <> tail:
      health(tail)
    case Failed{} <> _:
      Failed{}

# bp_run's loop: each native step (start, resume, or dropping the continuation
# after a Python exception) is followed by attached Python work: performing the
# requested effect, or raising. C stops after the first native failure; the
# model also allows later rounds, so its laws cover that too.
def rounds(+detach: Bool, outcomes: List<&2, Outcome>) -> List<Operation>:
  match outcomes:
    case []:
      []
    case outcome <> tail:
      List.append(&1, Operation, native_call(detach, outcome), PythonEffect{} <> rounds(detach, tail))

# bp_run: lease, run any number of rounds, then return the lease (marked by
# the rounds' health) before releasing the arena.
def invocation(+slot: Nat, detach: Bool, +outcomes: List<&2, Outcome>, small_heap: Bool, cache_space: Bool) -> List<Operation>:
  List.append(&1, Operation, acquire_call(slot),
    List.append(&1, Operation, rounds(detach, outcomes),
      return_call(slot, health(outcomes), small_heap, cache_space)))
