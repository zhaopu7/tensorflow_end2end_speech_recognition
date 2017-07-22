#! /usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import sys
import time
import tensorflow as tf
from tensorflow.python import debug as tf_debug

sys.path.append('../../')
from models.ctc.load_model import load
from models.test.util import measure_time
from models.test.data import generate_data, num2alpha, num2phone
from experiments.utils.sparsetensor import sparsetensor2list
from experiments.utils.parameter import count_total_parameters
from experiments.utils.training.learning_rate_controller.step import Controller


class TestCTC(tf.test.TestCase):

    @measure_time
    def test_ctc(self):
        print("CTC Working check.")
        self.check_training(model_type='blstm_ctc', label_type='phone')
        self.check_training(model_type='blstm_ctc', label_type='character')

        self.check_training(model_type='lstm_ctc', label_type='phone')
        self.check_training(model_type='lstm_ctc', label_type='character')

        self.check_training(model_type='bgru_ctc', label_type='phone')
        self.check_training(model_type='bgru_ctc', label_type='character')

        self.check_training(model_type='gru_ctc', label_type='phone')
        self.check_training(model_type='gru_ctc', label_type='character')

        # self.check_training(model_type='cnn_ctc', label_type='phone')
        # self.check_training(model_type='cnn_ctc', label_type='character')

    def check_training(self, model_type, label_type):
        print('----- ' + model_type + ', ' + label_type + ' -----')
        tf.reset_default_graph()
        with tf.Graph().as_default():
            # Load batch data
            batch_size = 4
            inputs, labels_true_st, inputs_seq_len = generate_data(
                label_type=label_type,
                model='ctc',
                batch_size=batch_size)

            # Define placeholders
            inputs_pl = tf.placeholder(tf.float32,
                                       shape=[None, None, inputs[0].shape[-1]],
                                       name='inputs')
            indices_pl = tf.placeholder(tf.int64, name='indices')
            values_pl = tf.placeholder(tf.int32, name='values')
            shape_pl = tf.placeholder(tf.int64, name='shape')
            labels_pl = tf.SparseTensor(indices_pl, values_pl, shape_pl)
            inputs_seq_len_pl = tf.placeholder(tf.int64,
                                               shape=[None],
                                               name='inputs_seq_len')
            keep_prob_input_pl = tf.placeholder(tf.float32,
                                                name='keep_prob_input')
            keep_prob_hidden_pl = tf.placeholder(tf.float32,
                                                 name='keep_prob_hidden')
            learning_rate_pl = tf.placeholder(tf.float32,
                                              name='learning_rate')

            # Define model graph
            num_classes = 26 if label_type == 'character' else 61
            model = load(model_type=model_type)
            network = model(batch_size=batch_size,
                            input_size=inputs[0].shape[-1],
                            num_unit=256,
                            num_layer=2,
                            bottleneck_dim=0,
                            num_classes=num_classes,
                            parameter_init=0.1,
                            clip_grad=5.0,
                            clip_activation=50,
                            dropout_ratio_input=1.0,
                            dropout_ratio_hidden=1.0,
                            num_proj=None,
                            weight_decay=1e-6)

            # Add to the graph each operation
            loss_op, logits = network.compute_loss(inputs_pl,
                                                   labels_pl,
                                                   inputs_seq_len_pl,
                                                   keep_prob_input_pl,
                                                   keep_prob_hidden_pl)
            train_op = network.train(loss_op,
                                     optimizer='rmsprop',
                                     learning_rate=learning_rate_pl)
            decode_op = network.decoder(logits,
                                        inputs_seq_len_pl,
                                        decode_type='beam_search',
                                        beam_width=20)
            ler_op = network.compute_ler(decode_op, labels_pl)

            # Define learning rate controller
            learning_rate = 1e-3
            lr_controller = Controller(learning_rate_init=learning_rate,
                                       decay_start_step=10,
                                       decay_steps=20,
                                       decay_rate=0.99,
                                       lower_better=True)

            # Add the variable initializer operation
            init_op = tf.global_variables_initializer()

            # Count total parameters
            parameters_dict, total_parameters = count_total_parameters(
                tf.trainable_variables())
            for parameter_name in sorted(parameters_dict.keys()):
                print("%s %d" %
                      (parameter_name, parameters_dict[parameter_name]))
            print("Total %d variables, %s M parameters" %
                  (len(parameters_dict.keys()),
                   "{:,}".format(total_parameters / 1000000)))

            # Make feed dict
            feed_dict = {
                inputs_pl: inputs,
                labels_pl: labels_true_st,
                inputs_seq_len_pl: inputs_seq_len,
                keep_prob_input_pl: network.dropout_ratio_input,
                keep_prob_hidden_pl: network.dropout_ratio_hidden
            }

            map_file_path = '../../experiments/timit/metrics/mapping_files/ctc/phone61_to_num.txt'

            with tf.Session() as sess:
                # Initialize parameters
                sess.run(init_op)

                # Wrapper for tfdbg
                # sess = tf_debug.LocalCLIDebugWrapperSession(sess)

                # Train model
                max_steps = 400
                start_time_global = time.time()
                start_time_step = time.time()
                ler_train_pre = 1
                not_improved_count = 0
                for step in range(max_steps):

                    learning_rate = lr_controller.decay_lr(
                        learning_rate=learning_rate,
                        step=step,
                        value=ler_train_pre)
                    feed_dict[learning_rate_pl] = learning_rate

                    # Compute loss
                    _, loss_train = sess.run(
                        [train_op, loss_op], feed_dict=feed_dict)

                    # Gradient check
                    # grads = sess.run(network.clipped_grads,
                    #                  feed_dict=feed_dict)
                    # for grad in grads:
                    #     print(np.max(grad))

                    if (step + 1) % 10 == 0:
                        # Change to evaluation mode
                        feed_dict[keep_prob_input_pl] = 1.0
                        feed_dict[keep_prob_hidden_pl] = 1.0

                        # Compute accuracy
                        ler_train = sess.run(ler_op, feed_dict=feed_dict)

                        duration_step = time.time() - start_time_step
                        print('Step %d: loss = %.3f / ler = %.4f (%.3f sec) / lr = %.5f' %
                              (step + 1, loss_train, ler_train, duration_step, learning_rate))
                        start_time_step = time.time()

                        # Visualize
                        labels_pred_st = sess.run(
                            decode_op, feed_dict=feed_dict)
                        labels_true = sparsetensor2list(labels_true_st,
                                                        batch_size=batch_size)
                        labels_pred = sparsetensor2list(labels_pred_st,
                                                        batch_size=batch_size)
                        if label_type == 'character':
                            print('True: %s' % num2alpha(labels_true[0]))
                            print('Pred: %s' % num2alpha(labels_pred[0]))
                        else:
                            print('True: %s' % num2phone(
                                labels_true[0], map_file_path))
                            print('Pred: %s' % num2phone(
                                labels_pred[0], map_file_path))

                        if ler_train >= ler_train_pre:
                            not_improved_count += 1
                        else:
                            not_improved_count = 0
                        if not_improved_count >= 5:
                            print('Modle is Converged.')
                            break
                        ler_train_pre = ler_train

                duration_global = time.time() - start_time_global
                print('Total time: %.3f sec' % (duration_global))


if __name__ == "__main__":
    tf.test.main()
