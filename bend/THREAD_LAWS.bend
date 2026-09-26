import Base
import ./thread_state.bend as Thread

law detached_effect_rejected:
  {Thread.step(Thread.Detached{}, Thread.PythonEffect{}) == None{} : Maybe<Thread.State>}

law accepted_effect_attached:
  for state: Thread.State
  for next: Thread.State
  for accepted: {Thread.step(state, Thread.PythonEffect{}) == Some{next} : Maybe<Thread.State>}
  {state == Thread.Attached{} : Thread.State}

law save_restore:
  {Thread.run([Thread.Save{}, Thread.Restore{}], Thread.Attached{}) == Some{Thread.Attached{}} : Maybe<Thread.State>}

# Quantifying over Outcome covers success, runtime failure, and prior poisoning.
law native_returns_attached:
  for release_gil: Bool
  for outcome: Thread.Outcome
  {Thread.run(Thread.native_call(release_gil, outcome), Thread.Attached{}) == Some{Thread.Attached{}} : Maybe<Thread.State>}

law native_then_effect_accepted:
  for release_gil: Bool
  for outcome: Thread.Outcome
  {Thread.run(List.append(&1, Thread.Operation, Thread.native_call(release_gil, outcome), [Thread.PythonEffect{}]), Thread.Attached{}) == Some{Thread.Attached{}} : Maybe<Thread.State>}

law release_before_restore_rejected:
  for outcome: Thread.Outcome
  {Thread.run([Thread.Save{}, Thread.WaitRuntime{}, Thread.NativeWork{outcome}, Thread.PythonEffect{}], Thread.Attached{}) == None{} : Maybe<Thread.State>}
