import torch
import torch.nn as nn
import torch.nn.functional as F

import global_variables

import bleu_score

"""
This .py file contains the following classes/functions:

Classes:
    EncoderRNN(input_size, embed_dim, hidden_size,n_layers, rnn_type = 'lstm', device = 'cuda')
    Attention_Module(hidden_dim, output_dim, device = 'cuda')
    AttentionDecoderRNN(output_size, embed_dim, hidden_size, n_layers = 1, attention = True, device = 'cuda')

Functions:
    Embedding(num_embeddings, embedding_dim, padding_idx)
    LSTM(input_size, hidden_size, **kwargs)
    LSTMCell(input_size, hidden_size, **kwargs)
    Linear(in_features, out_features, bias=True, dropout=0)
    sequence_mask(sequence_length, max_len=None, device = 'cuda')
"""


device = global_variables.device;
PAD_IDX = global_variables.PAD_IDX;


def Embedding(num_embeddings, embedding_dim, padding_idx):
    m = nn.Embedding(num_embeddings, embedding_dim, padding_idx=padding_idx)
    nn.init.uniform_(m.weight, -0.1, 0.1)
    nn.init.constant_(m.weight[padding_idx], 0)
    return m


def LSTM(input_size, hidden_size, **kwargs):
    m = nn.LSTM(input_size, hidden_size,**kwargs)
    for name, param in m.named_parameters():
        if 'weight' in name or 'bias' in name:
            param.data.uniform_(-0.1, 0.1)
    return m


def LSTMCell(input_size, hidden_size, **kwargs):
    m = nn.LSTMCell(input_size, hidden_size,**kwargs)
    for name, param in m.named_parameters():
        if 'weight' in name or 'bias' in name:
            param.data.uniform_(-0.1, 0.1)
    return m


def Linear(in_features, out_features, bias=True, dropout=0):
    """Linear layer (input: N x T x C)"""
    m = nn.Linear(in_features, out_features, bias=bias)
    m.weight.data.uniform_(-0.1, 0.1)
    if bias:
        m.bias.data.uniform_(-0.1, 0.1)
    return m


def sequence_mask(sequence_length, max_len=None, device = 'cuda'):
    if max_len is None:
        max_len = sequence_length.max().item()
    batch_size = sequence_length.size(0)
    seq_range = torch.arange(0, max_len).long()
    seq_range_expand = seq_range.unsqueeze(0).repeat([batch_size,1])
    seq_range_expand = seq_range_expand.to(device)
    seq_length_expand = (sequence_length.unsqueeze(1)
                         .expand_as(seq_range_expand))
    return (seq_range_expand < seq_length_expand).float()


class EncoderRNN(nn.Module):
    def __init__(self, input_size, embed_dim, hidden_size,n_layers, rnn_type = 'lstm', device = 'cuda'):
        super(EncoderRNN, self).__init__()
        self.hidden_size = hidden_size
        self.embedding = Embedding(input_size, embed_dim, PAD_IDX)
        self.rnn_type =  rnn_type
        self.dropout_in = nn.Dropout(p = 0.1)
        self.n_layers = n_layers
        self.device = device
        if rnn_type == 'gru':
            self.rnn = nn.GRU(embed_dim, hidden_size,batch_first=True,bidirectional=True, num_layers = self.n_layers, dropout = 0.2)
        elif rnn_type == 'lstm':
            self.rnn = LSTM(embed_dim, hidden_size, batch_first=True,bidirectional=True, num_layers = n_layers,dropout = 0.2)

    def forward(self, enc_inp, src_len):
        sorted_idx = torch.sort(src_len, descending=True)[1]
        orig_idx = torch.sort(sorted_idx)[1]
        embedded = self.embedding(enc_inp)
        bs = embedded.size(0)
        output = self.dropout_in(embedded)
        if self.rnn_type == 'gru':
            # hidden, c =  self.initHidden(bs)
            # sorted_output = output[sorted_idx]
            # sorted_len = src_len[sorted_idx]
            # packed_output = nn.utils.rnn.pack_padded_sequence(sorted_output, sorted_len.data.tolist(), batch_first = True)
            # packed_outs, hiddden = self.rnn(packed_output,(hidden, c))
            # hidden = hidden[:,orig_idx,:]
            # output, _ = nn.utils.rnn.pad_packed_sequence(packed_outs, padding_value=PAD_IDX, batch_first = True)
            # output = output[orig_idx]
            # hidden = hidden.view(self.n_layers, 2, bs, -1).transpose(1, 2).contiguous().view(self.n_layers, bs, -1)
            # return output, hidden, hidden
            hidden =  self.initHidden(bs)
            sorted_output = output[sorted_idx]
            sorted_len = src_len[sorted_idx]
            packed_output = nn.utils.rnn.pack_padded_sequence(sorted_output, sorted_len.data.tolist(), batch_first = True)
            packed_outs, hidden = self.rnn(packed_output, hidden)
            hidden = hidden[:,orig_idx,:]
            output, _ = nn.utils.rnn.pad_packed_sequence(packed_outs, padding_value=PAD_IDX, batch_first = True)
            output = output[orig_idx]
        elif self.rnn_type == 'lstm':
            hidden, c = self.initHidden(bs)
            sorted_output = output[sorted_idx]
            sorted_len = src_len[sorted_idx]
            packed_output = nn.utils.rnn.pack_padded_sequence(sorted_output, sorted_len.data.tolist(), batch_first = True)
            packed_outs, (hiddden, c) = self.rnn(packed_output,(hidden, c))
            hidden = hidden[:,orig_idx,:]
            c = c[:,orig_idx,:]
            output, _ = nn.utils.rnn.pad_packed_sequence(packed_outs, padding_value=PAD_IDX, batch_first = True)
            output = output[orig_idx]
            c = c.view(self.n_layers, 2, bs, -1).transpose(1, 2).contiguous().view(self.n_layers, bs, -1)
            hidden = hidden.view(self.n_layers, 2, bs, -1).transpose(1, 2).contiguous().view(self.n_layers, bs, -1)
            return output, hidden, c

    def initHidden(self,bs):
        if self.rnn_type == 'gru' :
            return torch.zeros(self.n_layers*2, bs, self.hidden_size).to(self.device)
        elif self.rnn_type == 'lstm':
            return torch.zeros(self.n_layers*2,bs,self.hidden_size).to(self.device),torch.zeros(self.n_layers*2,bs,self.hidden_size).to(self.device)

class Attention_Module(nn.Module):
    def __init__(self, hidden_dim, output_dim, device = 'cuda'):
        super(Attention_Module, self).__init__()
        self.l1 = Linear(hidden_dim, output_dim, bias = False)
        self.l2 = Linear(hidden_dim + output_dim, output_dim, bias =  False)
        self.device = device

    def forward(self, hidden, encoder_outs, src_lens):
        '''
        hiddden: bsz x hidden_dim
        encoder_outs: bsz x sq_len x encoder dim (output_dim)
        src_lens: bsz

        x: bsz x output_dim
        attn_score: bsz x sq_len
        '''
        x = self.l1(hidden)

        # weighted sum of encoder's hidden states
        #
        # encoder_outs.transpose(0,1) dim = sq_len x bsz x output_dim
        # x.unsqueeze(0) dim = 1 x bsz x output_dim
        att_score = (encoder_outs.transpose(0,1) * x.unsqueeze(0)).sum(dim = 2)

        # mask all following tokens in the sequence to facilitate learning
        seq_mask = sequence_mask(src_lens, max_len = max(src_lens).item(), device = self.device).transpose(0,1)
        masked_att = seq_mask*att_score
        masked_att[masked_att==0] = -1e10

        # get softmax of masked_att to get attention weights
        attn_scores = F.softmax(masked_att, dim=0)

        # multiply attention weights by encoder's hidden states to get context vector
        x = (attn_scores.unsqueeze(2) * encoder_outs.transpose(0,1)).sum(dim=0)
        x = torch.tanh(self.l2(torch.cat((x, hidden), dim=1)))
        return x, attn_scores

class AttentionDecoderRNN(nn.Module):
    def __init__(self, output_size, embed_dim, hidden_size, n_layers = 1, attention = True, device = 'cuda'):
        super(AttentionDecoderRNN, self).__init__()
        self.hidden_size = hidden_size
        encoder_output_size = hidden_size
        self.embedding = Embedding(output_size, embed_dim, PAD_IDX)
        self.dropout = nn.Dropout(p=0.1)
        self.n_layers = n_layers
        self.device = device
        self.att_layer = Attention_Module(self.hidden_size, encoder_output_size,self.device) if attention else None
        self.layers = nn.ModuleList([
            LSTMCell(
                input_size=self.hidden_size + embed_dim if ((layer == 0) and attention) else embed_dim if layer == 0 else hidden_size,
                hidden_size=hidden_size,
            )
            for layer in range(self.n_layers)
        ])
        self.fc_out = nn.Linear(self.hidden_size, output_size)
        self.softmax = nn.LogSoftmax(dim=1)

    def forward(self, dec_input,context_vector, prev_hiddens,prev_cs,encoder_outputs,src_len):
        bsz = dec_input.size(0)
        output = self.embedding(dec_input)
        output = self.dropout(output)

        # at each timestep of decoder, decoder takes in
        # (i) previous timestep's hidden state (output?),
        # (ii) previous output, and
        # (iii) context vector (context_vector)

        # concatenate the context vector with the decoder's previous hidden state
        if self.att_layer is not None:
            cated_input = torch.cat([output.squeeze(1),context_vector], dim = 1)
        else:
            cated_input = output.squeeze(1)
        new_hiddens = []
        new_cs = []
        for i, rnn in enumerate(self.layers):
            hidden, c = rnn(cated_input, (prev_hiddens[i], prev_cs[i]))
            cated_input = self.dropout(hidden)
            new_hiddens.append(hidden.unsqueeze(0))
            new_cs.append(c.unsqueeze(0))
        new_hiddens = torch.cat(new_hiddens, dim = 0)
        new_cs = torch.cat(new_cs, dim = 0)

        # apply attention using the last layer's hidden state
        if self.att_layer is not None:
            out, attn_score = self.att_layer(hidden, encoder_outputs, src_len)
        else:
            out = hidden
            attn_score = None
        context_vec = out
        out = self.dropout(out)
        out_vocab = self.softmax(self.fc_out(out))

        return out_vocab, context_vec, new_hiddens, new_cs, attn_score
