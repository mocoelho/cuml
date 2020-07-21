#
# Copyright (c) 2020, NVIDIA CORPORATION.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import math
import cupy as cp
from cuml.common import with_cupy_rmm
from cuml.common.kernel_utils import cuda_kernel_factory


def _map_l1_norm_kernel(dtype):
    """Creates cupy RawKernel for csr_raw_normalize_l1 function."""

    map_kernel_str = r'''
    ({0} *data, {1} *indices, {2} *indptr, int n_samples) {

      int tid = blockDim.x * blockIdx.x + threadIdx.x;

      if(tid >= n_samples) return;
      {0} sum = 0.0;


      for(int i = indptr[tid]; i < indptr[tid+1]; i++) {
        sum += fabs(data[i]);
      }


      if(sum == 0) return;

      for(int i = indptr[tid]; i < indptr[tid+1]; i++) {
        data[i] /= sum;
      }
    }
    '''
    return cuda_kernel_factory(map_kernel_str, dtype, "map_l1_norm_kernel")


def _map_l2_norm_kernel(dtype):
    """Creates cupy RawKernel for csr_raw_normalize_l2 function."""

    map_kernel_str = r'''
    ({0} *data, {1} *indices, {2} *indptr, int n_samples) {

      int tid = blockDim.x * blockIdx.x + threadIdx.x;

      if(tid >= n_samples) return;
      {0} sum = 0.0;

      for(int i = indptr[tid]; i < indptr[tid+1]; i++) {
        sum += (data[i] * data[i]);
      }

      if(sum == 0) return;

      sum = sqrt(sum);

      for(int i = indptr[tid]; i < indptr[tid+1]; i++) {
        data[i] /= sum;
      }
    }
    '''
    return cuda_kernel_factory(map_kernel_str, dtype, "map_l2_norm_kernel")


def _map_mul_sparse_array(dtype):
    """Creates cupy RawKernel for multiplying sparse array with a dense ar """

    map_kernel_str = r'''
    ({0} *data, {1} *indices, {2} *indptr, {3} *dense_ar, int data_size) {

      int tid = blockDim.x * blockIdx.x + threadIdx.x;

      if(tid >= data_size) return;

      int index = indices[tid];
      data[tid] =  data[tid] * dense_ar[index];
    }
    '''
    return cuda_kernel_factory(map_kernel_str, dtype, "_map_mul_sparse_array")


@with_cupy_rmm
def csr_row_normalize_l1(X, inplace=True):
    """Row normalize for csr matrix using the l1 norm"""
    if not inplace:
        X = X.copy()

    kernel = _map_l1_norm_kernel((X.dtype, X.indices.dtype, X.indptr.dtype))
    kernel((math.ceil(X.shape[0] / 32),), (32,),
           (X.data, X.indices, X.indptr, X.shape[0]))

    return X


@with_cupy_rmm
def csr_row_normalize_l2(X, inplace=True):
    """Row normalize for csr matrix using the l2 norm"""
    if not inplace:
        X = X.copy()

    kernel = _map_l2_norm_kernel((X.dtype, X.indices.dtype, X.indptr.dtype))
    kernel((math.ceil(X.shape[0] / 32),), (32,),
           (X.data, X.indices, X.indptr, X.shape[0]))

    return X


@with_cupy_rmm
def csr_diag_mul(X, y, inplace=True):
    """Multiply a sparse X matrix with diagonal matrix y"""
    if not inplace:
        X = X.copy()
    y = y.data
    kernel = _map_mul_sparse_array((X.dtype, X.indices.dtype,
                                   X.indptr.dtype, y.dtype))
    kernel((math.ceil(X.indices.shape[0] / 32),), (32,),
           (X.data, X.indices, X.indptr, y, X.indices.shape[0]))

    return X


def create_csr_matrix_from_count_df(count_df, empty_doc_ids, n_doc, n_features,
                                    dtype=cp.float32):
    """
    Create a sparse matrix from the count of tokens by document

    Parameters
    ----------
        count_df = cudf.DataFrame({'count':..., 'doc_id':.., 'token':.. })
                    sorted by doc_id and token
        empty_doc_ids = cupy array containing doc_ids with no tokens
        n_doc: Total number of documents
        n_features: Number of features
        dtype: Output dtype
    """
    data = count_df["count"].values
    indices = count_df["token"].values

    doc_token_counts = count_df["doc_id"].value_counts().reset_index()
    del count_df
    doc_token_counts = doc_token_counts.rename(
        {"doc_id": "token_counts", "index": "doc_id"}, axis=1
    ).sort_values(by="doc_id")

    token_counts = _insert_zeros(
        doc_token_counts["token_counts"], empty_doc_ids
    )
    indptr = token_counts.cumsum()
    indptr = cp.pad(indptr, (1, 0), "constant")

    return cp.sparse.csr_matrix(
        arg1=(data, indices, indptr), dtype=dtype,
        shape=(n_doc, n_features)
    )


def _insert_zeros(ary, zero_indices):
    """
    Create a new array of len(ary + zero_indices) where zero_indices
    indicates indexes of 0s in the new array. Ary is used to fill the rest.

    Example:
        _insert_zeros([1, 2, 3], [1, 3]) => [1, 0, 2, 0, 3]
    """
    if len(zero_indices) == 0:
        return ary.values

    new_ary = cp.zeros((len(ary) + len(zero_indices)), dtype=cp.int32)

    # getting mask of non-zeros
    data_mask = ~cp.in1d(cp.arange(0, len(new_ary), dtype=cp.int32),
                         zero_indices)

    new_ary[data_mask] = ary
    return new_ary
