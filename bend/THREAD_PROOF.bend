import Base
import ./THREAD_LAWS.bend as Laws
import ./thread_state.bend as Thread

def Laws.detached_effect_rejected():
  {==}

def none_discriminator(value: Maybe<Thread.State>) -> Type:
  match value:
    case None{}:
      Unit
    case Some{_}:
      Empty

def none_not_some(-next: Thread.State, accepted: {None{} == Some{next} : Maybe<Thread.State>}) -> Empty:
  %accepted : none_discriminator(_)
  Unit{}

def Laws.accepted_effect_attached(state, next, accepted):
  match state:
    case Thread.Attached{}:
      {==}
    case Thread.Detached{}:
      Empty.absurd({Thread.Detached{} == Thread.Attached{} : Thread.State}, none_not_some(next, accepted))

def Laws.save_restore():
  {==}

def Laws.native_returns_attached(release_gil, outcome):
  match release_gil:
    case False{}:
      {==}
    case True{}:
      {==}

def Laws.native_then_effect_accepted(release_gil, outcome):
  match release_gil:
    case False{}:
      {==}
    case True{}:
      {==}

def Laws.release_before_restore_rejected(outcome):
  {==}
