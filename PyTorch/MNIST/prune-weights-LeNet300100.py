""" 
This code prune weights in AlexNet usin L-OBS
""" 
import os
import numpy as np
from datetime import datetime

from utils.prune import unfold_kernel, fold_weights, get_error, create_sparse_mul_graph

import torch
import tensorflow as tf

use_cuda = torch.cuda.is_available()
# -------------------------------------------- User Config ------------------------------------
# data path specify
dataset_name = 'MNIST'
model_name = 'LeNet300100'
hessian_inverse_root = './%s/hessian_inv' %(model_name) # Specify your hessian inverse root
save_root = './%s/pruned_weight' %(model_name) # Specify your sparse parameters save root
if not os.path.exists(save_root):
	os.makedirs(save_root)
mask_root = './%s/mask' %(model_name) # Specify your mask matrix storing root
if not os.path.exists(mask_root):
	os.makedirs(mask_root)
rank_root = './%s/sensitivity' %(model_name) # Specify your parameter rank file save root
if not os.path.exists(rank_root):
	os.makedirs(rank_root)
# use_tfbackend = False # Whether to use tensorflow backend in multiply weight and mask matrix
pretrain_model_path = './%s/%s_pretrain.pth'%(model_name, model_name) # Specify your pretrain model path
# Layer name of AlexNet
layer_name_list = [
	'fc1', 'fc2', 'fc3'
]
# -------------------------------------------- User Config ------------------------------------
pretrain = torch.load(pretrain_model_path)
for name, param in pretrain.items():
	pretrain[name] = param.cpu()

for layer_idx, layer_name in enumerate(layer_name_list):

	# if os.path.exists('%s/CR_5/%s.weight.npy' %(save_root, layer_name)):
	# 	continue

	print ('[%s] %s' %(datetime.now(), layer_name))
	# Specify layer type, C for convolution, F for fully-connected
	if layer_name.startswith('conv'):
		# layer_type = 'C'
		layer_type = 'R'
	elif layer_name.startswith('fc'):
		layer_type = 'F'
		# layer_type = 'F-'
	else:
		raise IOError

	if layer_type == 'C':
		kernel = pretrain['%s.weight' %layer_name].data.numpy()
		kernel_shape = kernel.shape # [64, 3, 11, 11]
		weight = unfold_kernel(kernel) # [364, 64]
		bias = pretrain['%s.bias' %layer_name].data.numpy() # [64, ]
		wb = np.concatenate([weight, bias.reshape(1, -1)], axis = 0) # [365, 64]
	elif layer_type == 'F':
		weight = pretrain['%s.weight' %layer_name].data.numpy()
		bias = pretrain['%s.bias' %layer_name].data.numpy()
		wb = np.hstack([weight, bias.reshape(-1, 1)]).transpose()

	layer_error = dict()
	hessian_inv = np.load('%s/%s.npy' %(hessian_inverse_root, layer_name))

	l1, l2 = wb.shape

	# Rank the sensitivity
	if os.path.exists('%s/%s.npy' %(rank_root, layer_name)):
		print ('Weight rank exist, loading.')
		sen_rank = np.load('%s/%s.npy' %(rank_root, layer_name))
	else:
		print ('Weight rank not exist, create one.')
		# Record sensitivity of each elements
		L = np.zeros([l1 * l2])
		for row_idx in range(l1):
			for col_idx in range(l2):
				L[row_idx * l2 + col_idx] = np.power(wb[row_idx, col_idx], 2) / (hessian_inv[row_idx, row_idx] + 10e-6)

		sen_rank = np.argsort(L)
		np.save('%s/%s.npy' %(rank_root, layer_name), sen_rank)
	
	n_prune = l1 * l2
	save_interval = int(n_prune / 20) # 5% as save gap
	print ('[%s] Prune number: %d' % (datetime.now(), n_prune))
	if n_prune > 10e6:
		print('Woops... Since you got lots of weights to prune, a little more time may needed.')

	mask = np.ones(wb.shape)
	for i in range(n_prune):
		
		prune_idx = sen_rank[i]
		prune_row_idx = int(prune_idx / l2)
		prune_col_idx = prune_idx % l2
		delta_W = - wb[prune_row_idx, prune_col_idx] / (hessian_inv[prune_row_idx, prune_row_idx] + 10e-6) \
			* hessian_inv[:, prune_row_idx]
		wb[:, prune_col_idx] += delta_W
		mask[prune_row_idx, prune_col_idx] = 0
		# wb = np.multiply(wb, mask)

		if i % save_interval == 0 and int(i / save_interval) >= 4:
			wb = np.multiply(wb, mask)
			CR = 100 - int((i / save_interval)) * 5
			print('[%s] Now save pruned weights of CR %d' %(datetime.now(), CR))

			# Save pruned weights and mask matrix
			if not os.path.exists('%s/CR_%s' %(save_root, CR)):
				os.makedirs('%s/CR_%s' %(save_root, CR))
			if not os.path.exists('%s/CR_%s' %(mask_root, CR)):
				os.makedirs('%s/CR_%s' %(mask_root, CR))
			
			if layer_type == 'F':
				np.save('%s/CR_%s/%s.weight' %(save_root, CR, layer_name), wb[0: -1, :].transpose())
				np.save('%s/CR_%s/%s.bias' %(save_root, CR, layer_name), wb[-1, :].transpose())
				
				np.save('%s/CR_%s/%s.weight' %(mask_root, CR, layer_name), mask[0: -1, :].transpose())
				np.save('%s/CR_%s/%s.bias' %(mask_root, CR, layer_name), mask[-1, :].transpose())
			elif layer_type == 'C':
				kernel = fold_weights(wb[0 :-1, :], kernel_shape)
				bias = wb[-1, :]
				np.save('%s/CR_%s/%s.weight' %(save_root, CR, layer_name), kernel)
				np.save('%s/CR_%s/%s.bias' %(save_root, CR, layer_name), bias)

				mask_kernel = fold_weights(mask[0 :-1, :], kernel_shape)
				mask_bias = mask[-1, :]
				np.save('%s/CR_%s/%s.weight' %(mask_root, CR, layer_name), mask_kernel)
				np.save('%s/CR_%s/%s.bias' %(mask_root, CR, layer_name), mask_bias)
			elif layer_type == 'R':
				kernel = fold_weights(wb, kernel_shape)
				np.save('%s/CR_%s/%s.weight' %(save_root, CR, layer_name), kernel)

				mask_kernel = fold_weights(mask, kernel_shape)
				np.save('%s/CR_%s/%s.weight' %(mask_root, CR, layer_name), mask_kernel)
			if CR == 5:
				break
	