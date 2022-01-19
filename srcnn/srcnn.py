import os
import time
import sys

import numpy as np
import tensorflow as tf
import matplotlib
import matplotlib.pyplot as plt
import pandas as pd

from . import utils

def _maybe_pad_x(x, padding, is_training):
    if padding == 0:
        x_pad = x
    elif padding > 0:
        x_pad = tf.cond(is_training, lambda: x,
                        lambda: utils.replicate_padding(x, padding))
    else:
        raise ValueError("Padding value %i should be greater than or equal to 1" % padding)
    return x_pad

class SRCNN:
    def __init__(self, x, y, layer_sizes, filter_sizes, input_depth=1,
                 learning_rate=1e-4,
                 gpu=True, upscale_factor=2, output_depth=1, is_training=True):
        '''
        Args:
            layer_sizes: Sizes of each layer
            filter_sizes: List of sizes of convolutional filters
            input_depth: Number of channels in input
        '''
        self.x = x
        self.y = y
        self.is_training = is_training
        self.upscale_factor = upscale_factor
        self.layer_sizes = layer_sizes
        self.filter_sizes = filter_sizes
        self.input_depth = input_depth
        self.output_depth = output_depth
        self.learning_rate = learning_rate
        if gpu:
            self.device = '/gpu:0'
        else:
            self.device = '/cpu:0'

        self.global_step = tf.Variable(0, trainable=False)
        self.learning_rate = tf.compat.v1.train.exponential_decay(learning_rate, self.global_step, 100000, 0.96)
        self._build_graph()

    def _normalize(self):
        with tf.compat.v1.variable_scope("normalize_inputs", reuse=tf.compat.v1.AUTO_REUSE) as scope:
            self.x_norm = tf.compat.v1.layers.batch_normalization(
                self.x, trainable=False, epsilon=1e-6, center=False, scale=False, training=self.is_training)
        with tf.compat.v1.variable_scope("normalize_labels", reuse=tf.compat.v1.AUTO_REUSE) as scope:
            self.y_norm = tf.compat.v1.layers.batch_normalization(
                self.y, trainable=False, epsilon=1e-6, scale=False, training=self.is_training)     
            scope.reuse_variables()
            self.y_mean = tf.compat.v1.get_variable('batch_normalization/moving_mean')
            self.y_variance = tf.compat.v1.get_variable('batch_normalization/moving_variance')
            self.y_beta = tf.compat.v1.get_variable('batch_normalization/beta')

    def _inference(self, X):
        for i, k in enumerate(self.filter_sizes):
            with tf.compat.v1.variable_scope("hidden_%i" % i) as scope:
                if i == (len(self.filter_sizes)-1):
                    activation = None
                else:
                    activation = tf.nn.relu
                pad_amt = (k-1)/2
                X = _maybe_pad_x(X, pad_amt, self.is_training)
                X = tf.compat.v1.layers.conv2d(X, self.layer_sizes[i], k, activation=activation)
        return X

    def _loss(self, predictions):
        with tf.name_scope("loss"):
            err = tf.square(predictions - self.y_norm)
            err_filled = utils.fill_na(err, 0)
            finite_count = tf.reduce_sum(tf.cast(tf.math.is_finite(err), tf.float32))
            mse = tf.reduce_sum(err_filled) / finite_count
            return mse

    def _optimize(self):
        opt1 = tf.compat.v1.train.AdamOptimizer(self.learning_rate)
        opt2 = tf.compat.v1.train.AdamOptimizer(self.learning_rate*0.1)

        # compute gradients irrespective of optimizer
        grads = opt1.compute_gradients(self.loss)

        # apply gradients to first n-1 layers 
        opt1_grads = [v for v in grads if "hidden_%i" % (len(self.filter_sizes)-1)
                    not in v[0].op.name]
        opt2_grads = [v for v in grads if "hidden_%i" % (len(self.filter_sizes)-1)
                    in v[0].op.name]

        self.opt = tf.group(opt1.apply_gradients(opt1_grads, global_step=self.global_step),
                            opt2.apply_gradients(opt2_grads))

    def _summaries(self):
        # tf.contrib.layers.summarize_tensors(tf.trainable_variables())
        tf.summary.scalar('loss', self.loss)
        tf.summary.scalar('rmse', self.rmse)

    def _build_graph(self):
        self._normalize()
        with tf.device(self.device):
            _prediction_norm = self._inference(self.x_norm)
            self.loss = self._loss(_prediction_norm)

            self._optimize()
        self.prediction = _prediction_norm * tf.sqrt(self.y_variance) - self.y_mean
        self.rmse = tf.sqrt(utils.nanmean(tf.square(self.prediction - self.y)),
                            name='rmse')
        self._summaries()
