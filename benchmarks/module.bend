import Base
import ./bend/python.bend as Python
import ./bend/tensor.bend as Tensor
import ./bend/blas.bend as Blas

def negate(x: F32) -> F32:
  (0.0 - x : F32)

# Calling an existing kernel keeps CUDA tensors in PyTorch's ownership.
def invoke(request: Python.Call) -> IO(Python.Object):
  match request:
    case Python.PyCall{function <> arguments, keywords}:
      do IO<Python.Object>:
        Python.require_no_kwargs(keywords)
        Python.invoke(function, arguments)
    case _:
      Python.type_error(Python.Object, "expected a callable as the first argument")

def main() -> IO(Unit):
  do IO<Unit>:
    Tensor.export_numpy_f32_inplace(~negate, "numpy_negate", True{})
    Tensor.export_torch_f32_inplace(~negate, "torch_negate", True{})
    Blas.export_scale("numpy_scale", True{})
    Blas.export_torch_scale("torch_scale", True{})
    Blas.export_matmul("numpy_matmul", True{})
    Blas.export_torch_matmul("torch_matmul", True{})
    Python.export("invoke", invoke, True{})
