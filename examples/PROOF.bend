import Base
import ./LAWS.bend as Laws
import ./arithmetic.bend as Arithmetic

def Laws.identity(x):
  {==}

def Laws.square_wraps():
  {==}

def Laws.increment(x):
  {==}

def Laws.add_comm(x, y):
  U32.add_comm(x, y)

def Laws.flip_involution(x):
  match x:
    case False{}:
      {==}
    case True{}:
      {==}

def Laws.countdown_zero(acc):
  {==}

def Laws.countdown_step(n, acc):
  {==}

def Laws.rounds_zero(acc):
  {==}

def Laws.rounds_step(n, acc):
  {==}

def Laws.rounds_compose(a, b, acc):
  match a:
    case 0n:
      {==}
    case 1n+pred:
      Laws.rounds_compose(pred, b, ((acc * 1664525 : U32) + 1013904223 : U32))

def Laws.countdown_compose(a, b, acc):
  match a:
    case 0n:
      {==}
    case 1n+pred:
      Laws.countdown_compose(pred, b, Arithmetic.rounds(32n, acc))

def Laws.sum_append(xs, ys, acc):
  match xs:
    case []:
      {==}
    case head <> tail:
      Laws.sum_append(tail, ys, (acc + head : U32))
