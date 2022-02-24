"""Tests for layout_map."""

from keras import backend
from keras import layers
from keras.dtensor import layout_map as layout_map_lib
from keras.utils import tf_utils
import numpy as np
import tensorflow.compat.v2 as tf

from keras.dtensor.tests import test_util
from tensorflow.dtensor import python as dtensor  # pylint: disable=g-direct-tensorflow-import


class LayoutMapTest(test_util.DTensorBaseTest):

  def setUp(self):
    super(LayoutMapTest, self).setUp()
    backend.enable_tf_random_generator()
    tf_utils.set_random_seed(1337)
    global_ids = test_util.create_device_ids_array((2, 2))
    local_device_ids = np.ravel(global_ids).tolist()
    mesh_dict = {
        'CPU':
            dtensor.Mesh(['X', 'Y'], global_ids,
                         local_device_ids,
                         test_util.create_device_list((2, 2), 'CPU'))
    }
    self.mesh = self.configTestMesh(mesh_dict)
    self.layout_2d = dtensor.Layout.replicated(self.mesh, rank=2)
    self.layout_1d = dtensor.Layout.replicated(self.mesh, rank=1)

    self.sharded_2d = dtensor.Layout.batch_sharded(self.mesh, 'X', rank=2)
    self.sharded_1d = dtensor.Layout.batch_sharded(self.mesh, 'X', rank=1)

  def test_add(self):
    layout_map = layout_map_lib.LayoutMap()

    layout_map['dense/kernel'] = self.layout_2d
    layout_map['dense/bias'] = self.layout_1d

    # Make there are two items in the map, and we access them via the
    # underlying container at layout_map._layout_map
    self.assertLen(layout_map._layout_map, 2)
    self.assertEqual(layout_map._layout_map['dense/kernel'], self.layout_2d)
    self.assertEqual(layout_map._layout_map['dense/bias'], self.layout_1d)

    with self.assertRaisesRegex(ValueError, 'dense/kernel already exist'):
      layout_map['dense/kernel'] = self.layout_1d

    with self.assertRaisesRegex(ValueError, 'should be a dtensor.Layout'):
      layout_map['conv.kernel'] = [1, 2, 3]

  def test_get(self):
    layout_map = layout_map_lib.LayoutMap()

    layout_map['dense/kernel'] = self.sharded_2d
    layout_map['dense/bias'] = self.sharded_1d

    layout_map['dense.*kernel'] = self.layout_2d
    layout_map['dense.*bias'] = self.layout_1d

    layout_map['.*bias'] = self.sharded_1d

    self.assertEqual(layout_map['dense/kernel'], self.sharded_2d)
    self.assertEqual(layout_map['dense/bias'], self.sharded_1d)

    # Map against the wildcard bias rule for dense, and based on the order of
    # insertion, it will not use .*bias.
    self.assertEqual(layout_map['dense_2/kernel'], self.layout_2d)
    self.assertEqual(layout_map['dense_2/bias'], self.layout_1d)

    self.assertIsNone(layout_map['conv2d/kernel'])
    self.assertEqual(layout_map['conv2d/bias'], self.sharded_1d)

  def test_delete(self):
    layout_map = layout_map_lib.LayoutMap()

    layout_map['dense/kernel'] = self.layout_2d
    layout_map['dense/bias'] = self.layout_1d

    self.assertEqual(layout_map.pop('dense/kernel'), self.layout_2d)
    # Make sure to match against the exact string, not the regex
    with self.assertRaises(KeyError):
      layout_map.pop('.*bias')

    # Make sure del also works
    del layout_map['dense/bias']

    self.assertEmpty(layout_map._layout_map)

  def test_len(self):
    layout_map = layout_map_lib.LayoutMap()
    self.assertEmpty(layout_map)

    layout_map['dense/kernel'] = self.layout_2d
    layout_map['dense/bias'] = self.layout_1d

    self.assertLen(layout_map, 2)

  def test_iter(self):
    layout_map = layout_map_lib.LayoutMap()

    layout_map['dense/kernel'] = self.layout_2d
    layout_map['dense/bias'] = self.layout_1d

    # Make sure the items are ordered based on the insertion order.
    self.assertEqual(list(layout_map.keys()), ['dense/kernel', 'dense/bias'])

    keys = []
    values = []
    for k, v in layout_map.items():
      keys.append(k)
      values.append(v)

    self.assertEqual(keys, ['dense/kernel', 'dense/bias'])
    self.assertEqual(values, [self.layout_2d, self.layout_1d])


# Class used for testing.
class SubclassModel(tf.keras.Model):

  def __init__(self, name=None):
    super().__init__(name=name)
    self.d1 = layers.Dense(1000)
    self.d2 = layers.Dense(1000)
    self.dropout = layers.Dropout(0.1)

  def call(self, inputs, training=None):
    x = self.d1(inputs)
    x = self.dropout(x, training=training)
    return self.d2(x)


class ObjectPathMappingTest(test_util.DTensorBaseTest):

  def setUp(self):
    super(ObjectPathMappingTest, self).setUp()
    backend.enable_tf_random_generator()
    tf_utils.set_random_seed(1337)
    global_ids = test_util.create_device_ids_array((2, 2))
    local_device_ids = np.ravel(global_ids).tolist()
    mesh_dict = {
        'CPU':
            dtensor.Mesh(['X', 'Y'], global_ids,
                         local_device_ids,
                         test_util.create_device_list((2, 2), 'CPU'))
    }
    self.mesh = self.configTestMesh(mesh_dict)
    self.layout_2d = dtensor.Layout.replicated(self.mesh, rank=2)
    self.layout_1d = dtensor.Layout.replicated(self.mesh, rank=1)

    self.sharded_2d = dtensor.Layout.batch_sharded(self.mesh, 'X', rank=2)
    self.sharded_1d = dtensor.Layout.batch_sharded(self.mesh, 'X', rank=1)

  def test_init_subclass_model_variable_with_layout(self):
    layout_map = layout_map_lib.LayoutMap(mesh=self.mesh)
    layout_map['d1.kernel'] = self.layout_2d
    layout_map['d1.bias'] = self.layout_1d
    layout_map['d2.kernel'] = self.layout_2d
    layout_map['d2.bias'] = self.layout_1d

    with layout_map_lib.layout_map_scope(layout_map):
      model = SubclassModel(name='model')

    # Init the model with eager tensor, make sure the model weights have correct
    # layout, as well as produce correct result.
    inputs = tf.zeros((10, 10), layout=self.layout_2d)
    result = model(inputs)
    self.assertAllClose(result, tf.zeros((10, 1000)))
    d1 = model.d1
    d2 = model.d2
    self.assertEqual(d1.kernel.layout, self.layout_2d)
    self.assertEqual(d1.bias.layout, self.layout_1d)
    self.assertEqual(d2.kernel.layout, self.layout_2d)
    self.assertEqual(d2.bias.layout, self.layout_1d)

    # Also make sure we repopulate the cached attributes like
    # layer._trainable_weights
    self.assertIs(d1.kernel, d1._trainable_weights[0])
    self.assertIs(d1.bias, d1._trainable_weights[1])
    self.assertIs(d2.kernel, d2._trainable_weights[0])
    self.assertIs(d2.bias, d2._trainable_weights[1])

    result = model(tf.zeros((10, 10), layout=self.layout_2d), training=True)
    self.assertAllClose(result, tf.zeros((10, 1000), layout=self.layout_2d))

  def test_init_functional_model_variable_with_layout(self):
    # Note that the functional model is using layers name + attribute name
    # the layer name are unique among the functional model, and when the layer
    # doesn't have a name, keras will give it a unique name based on the layer
    # class.
    layout_map = layout_map_lib.LayoutMap(mesh=self.mesh)
    layout_map['d1.kernel'] = self.layout_2d
    layout_map['d1.bias'] = self.layout_1d
    layout_map['d2.kernel'] = self.layout_2d
    layout_map['d2.bias'] = self.layout_1d

    with layout_map_lib.layout_map_scope(layout_map):
      inputs = tf.keras.Input((10,), batch_size=10)
      x = layers.Dense(20, name='d1')(inputs)
      x = layers.Dropout(0.1)(x)
      output = layers.Dense(30, name='d2')(x)

      model = tf.keras.Model(inputs, output)

    # It includes input layer as well.
    self.assertLen(model.layers, 4)
    d1 = model.layers[1]
    d2 = model.layers[3]

    self.assertEqual(d1.kernel.layout, self.layout_2d)
    self.assertEqual(d1.bias.layout, self.layout_1d)
    self.assertEqual(d2.kernel.layout, self.layout_2d)
    self.assertEqual(d2.bias.layout, self.layout_1d)

    # Also make sure we repopulate the cached attributes like
    # layer._trainable_weights
    self.assertIs(d1.kernel, d1._trainable_weights[0])
    self.assertIs(d1.bias, d1._trainable_weights[1])
    self.assertIs(d2.kernel, d2._trainable_weights[0])
    self.assertIs(d2.bias, d2._trainable_weights[1])

    result = model(tf.zeros((10, 10), layout=self.layout_2d), training=True)
    self.assertAllClose(result, tf.zeros((10, 30), layout=self.layout_2d))

  def test_init_sequential_model_variable_with_layout(self):
    # Note that the sequential model is using layers name + attribute name
    # the layer name are unique among the functional model, and when the layer
    # doesn't have a name, keras will give it a unique name based on the layer
    # class.
    layout_map = layout_map_lib.LayoutMap(mesh=self.mesh)
    layout_map['d1.kernel'] = self.layout_2d
    layout_map['d1.bias'] = self.layout_1d
    layout_map['d2.kernel'] = self.layout_2d
    layout_map['d2.bias'] = self.layout_1d

    with layout_map_lib.layout_map_scope(layout_map):
      model = tf.keras.Sequential([
          layers.Dense(20, name='d1', input_shape=(10,)),
          layers.Dropout(0.1),
          layers.Dense(30, name='d2')
      ])

    self.assertLen(model.layers, 3)
    d1 = model.layers[0]
    d2 = model.layers[2]

    self.assertEqual(d1.kernel.layout, self.layout_2d)
    self.assertEqual(d1.bias.layout, self.layout_1d)
    self.assertEqual(d2.kernel.layout, self.layout_2d)
    self.assertEqual(d2.bias.layout, self.layout_1d)

    # Also make sure we repopulate the cached attributes like
    # layer._trainable_weights
    self.assertIs(d1.kernel, d1._trainable_weights[0])
    self.assertIs(d1.bias, d1._trainable_weights[1])
    self.assertIs(d2.kernel, d2._trainable_weights[0])
    self.assertIs(d2.bias, d2._trainable_weights[1])

    result = model(tf.zeros((10, 10), layout=self.layout_2d), training=True)
    self.assertAllClose(result, tf.zeros((10, 30), layout=self.layout_2d))


if __name__ == '__main__':
  tf.test.main()
