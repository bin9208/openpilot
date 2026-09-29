import base64
import pickle
from pathlib import Path

import numpy as np
import onnx
import pytest
from onnx import TensorProto, helper


def write_model(path, *, metadata=None, passthrough=False):
  inputs = [helper.make_tensor_value_info('new_img', TensorProto.FLOAT, [1, 4]),
            helper.make_tensor_value_info('state_x', TensorProto.FLOAT, [1, 4])]
  outputs = [helper.make_tensor_value_info('outputs', TensorProto.FLOAT, [1, 4]),
             helper.make_tensor_value_info('next_state_x', TensorProto.FLOAT, [1, 4])]
  nodes = [helper.make_node('Add', ['new_img', 'state_x'], ['sum']),
           helper.make_node('Contiguous' if passthrough else 'Identity', ['sum'], ['outputs'],
                            domain='org.tinygrad' if passthrough else ''),
           helper.make_node('Identity', ['sum'], ['next_state_x'])]
  model = helper.make_model(helper.make_graph(nodes, 'fixture', inputs, outputs),
                            opset_imports=[helper.make_opsetid('', 17), helper.make_opsetid('org.tinygrad', 1)])
  model.ir_version = 8
  props = {'model_checkpoint': 'fixture-checkpoint', 'output_slices': base64.b64encode(pickle.dumps({'plan': slice(0, 4)})).decode()}
  props.update(metadata or {})
  helper.set_model_props(model, props)
  onnx.save(model, str(path))
  return path


def test_prepare_runs_actual_onnx_and_preserves_source(tmp_path):
  import onnxruntime as ort
  from tools.jetlink_model.prepare import prepare
  source = write_model(tmp_path / 'input.onnx', passthrough=True)
  original = source.read_bytes()
  result = prepare(source, tmp_path / 'package', 4)
  assert result.manifest['source']['checkpoint'] == 'fixture-checkpoint'
  assert result.manifest['state_pairs'] == {'state_x': 'next_state_x'}
  session = ort.InferenceSession(str(result.model_path), providers=['CPUExecutionProvider'])
  values = session.run(None, {'new_img': np.array([[1, 2, 3, 4]], dtype=np.float32), 'state_x': np.ones((1, 4), dtype=np.float32)})
  np.testing.assert_array_equal(values[0], [[2, 3, 4, 5]])
  assert source.read_bytes() == original
  assert (tmp_path / 'package' / 'manifest.json').is_file()


def test_pickle_globals_are_never_executed(tmp_path):
  from tools.jetlink_model.prepare import prepare
  class Attack:
    def __reduce__(self): return (eval, ("'bad'",))
  source = write_model(tmp_path / 'bad.onnx', metadata={'output_slices': base64.b64encode(pickle.dumps(Attack())).decode()})
  with pytest.raises(ValueError, match='metadata'): prepare(source, tmp_path / 'package', 4)
  assert not (tmp_path / 'package').exists()


def test_existing_package_never_replaced(tmp_path):
  from tools.jetlink_model.prepare import prepare
  source = write_model(tmp_path / 'input.onnx')
  destination = tmp_path / 'package'
  destination.mkdir()
  (destination / 'sentinel').write_text('keep')
  with pytest.raises(FileExistsError): prepare(source, destination, 4)
  assert (destination / 'sentinel').read_text() == 'keep'


def test_external_data_escape_rejected_before_runtime(tmp_path):
  from tools.jetlink_model.prepare import prepare
  source = write_model(tmp_path / 'input.onnx')
  model = onnx.load(str(source))
  tensor = model.graph.initializer.add(name='external', data_type=TensorProto.FLOAT, dims=[1])
  tensor.data_location = TensorProto.EXTERNAL
  tensor.external_data.add(key='location', value='../outside.bin')
  onnx.save(model, str(source))
  with pytest.raises(ValueError, match='external'): prepare(source, tmp_path / 'package', 4)


@pytest.mark.parametrize('operation', ['Identity', 'Neg'])
def test_only_identity_contiguous_function_is_accepted(tmp_path, operation):
  from tools.jetlink_model.prepare import prepare
  source = write_model(tmp_path / 'input.onnx', passthrough=True)
  model = onnx.load(str(source))
  model.functions.append(helper.make_function('org.tinygrad', 'Contiguous', ['X'], ['Y'],
                         [helper.make_node(operation, ['X'], ['Y'])], [helper.make_opsetid('', 17)]))
  onnx.save(model, str(source))
  if operation == 'Identity':
    result = prepare(source, tmp_path / 'package', 4)
    assert result.manifest['source']['checkpoint'] == 'fixture-checkpoint'
    # ORT already expands this valid local function. Keeping the exact graph
    # prevents a different optimization/fusion path for fp16 arithmetic.
    assert result.model_path.read_bytes() == source.read_bytes()
  else:
    with pytest.raises(ValueError, match='function'):
      prepare(source, tmp_path / 'package', 4)


def test_exporter_line_wrapped_slice_metadata():
  from tools.jetlink_model.prepare import output_slices
  encoded = base64.encodebytes(pickle.dumps({'plan': slice(0, 18452)})).decode()
  assert output_slices(encoded) == {'plan': [0, 18452]}


def test_oversize_tensor_rejected_before_runtime_creation(tmp_path, monkeypatch):
  import onnxruntime
  from tools.jetlink_model.prepare import prepare
  source = write_model(tmp_path / 'input.onnx')
  model = onnx.load(str(source))
  model.graph.input[0].type.tensor_type.shape.dim[1].dim_value = 2**30 + 1
  onnx.save(model, str(source))
  def forbidden_runtime(*args, **kwargs):
    pytest.fail('oversized graph reached runtime allocation')
  monkeypatch.setattr(onnxruntime, 'InferenceSession', forbidden_runtime)
  with pytest.raises(ValueError, match='tensor'):
    prepare(source, tmp_path / 'package', 4)


def test_external_alias_expansion_budget_is_checked_before_loading(tmp_path, monkeypatch):
  import tools.jetlink_model.prepare as module
  source = write_model(tmp_path / 'input.onnx')
  model = onnx.load(str(source))
  for index in range(8):
    tensor = model.graph.initializer.add(name=f'alias{index}', data_type=TensorProto.FLOAT, dims=[64])
    tensor.data_location = TensorProto.EXTERNAL
    tensor.external_data.add(key='location', value='shared.bin')
    tensor.external_data.add(key='length', value='256')
  onnx.save(model, str(source))
  (tmp_path / 'shared.bin').write_bytes(bytes(256))
  monkeypatch.setattr(module, 'MAX_MODEL_BYTES', source.stat().st_size + 257)
  def forbidden_load(*args, **kwargs):
    pytest.fail('external aliases reached memory expansion')
  monkeypatch.setattr(onnx.external_data_helper, 'load_external_data_for_model', forbidden_load)
  with pytest.raises(ValueError, match='external'):
    module.prepare(source, tmp_path / 'package', 4)
