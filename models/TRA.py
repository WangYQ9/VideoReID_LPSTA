import torch
import torch.nn as nn
from torch.nn import functional as F

def weights_init_kaiming(m):
    classname = m.__class__.__name__
    if classname.find('Linear') != -1:
        nn.init.kaiming_normal_(m.weight, a=0, mode='fan_out')
        nn.init.constant_(m.bias, 0.0)
    elif classname.find('Conv') != -1:
        nn.init.kaiming_normal_(m.weight, a=0, mode='fan_in')
        if m.bias is not None:
            nn.init.constant_(m.bias, 0.0)
    elif classname.find('BatchNorm') != -1:
        if m.affine:
            nn.init.constant_(m.weight, 1.0)
            nn.init.constant_(m.bias, 0.0)

class TRAG(nn.Module):

    def __init__(self, inplanes, num):

        super(TRAG, self).__init__()

        self.inplanes = inplanes
        self.num = num

        self.relu = nn.ReLU(True)
        self.avg = nn.AdaptiveAvgPool2d((1, 1))

        if self.is_mutual_spatial_attention == 'yes':

            print('Build ' + self.num + ' layer mutual spatial attention!')

            self.gamma_temporal = nn.Sequential(
                nn.Conv2d(in_channels=inplanes, out_channels=int(inplanes / 16),
                          kernel_size=1, stride=1, padding=0, bias=False),
                nn.BatchNorm2d(int(inplanes / 16)),
                self.relu
            )
            self.gamma_temporal.apply(weights_init_kaiming)

            self.beta_temporal = nn.Sequential(
                nn.Conv2d(in_channels=inplanes, out_channels=int(inplanes / 16),
                          kernel_size=1, stride=1, padding=0, bias=False),
                nn.BatchNorm2d(int(inplanes / 16)),
                self.relu
            )
            self.beta_temporal.apply(weights_init_kaiming)

            self.gg_temporal = nn.Sequential(
                nn.Conv2d(in_channels=4 * 16 * 8, out_channels=128,
                          kernel_size=1, stride=1, padding=0, bias=False),
                nn.BatchNorm2d(128),
                self.relu,
            )
            self.gg_temporal.apply(weights_init_kaiming)

            self.te_para = nn.Sequential(
                nn.Conv2d(in_channels=2 * 128, out_channels=1,
                          kernel_size=1, stride=1, padding=0, bias=False),
                nn.BatchNorm2d(1),
                nn.Sigmoid()
            )
            self.te_para.apply(weights_init_kaiming)

        if self.is_mutual_channel_attention == 'yes':

            print('Build ' + self.num + ' layer mutual channel attention!')
            self.mid_channel = int(self.inplanes / 16)
            self.theta_channel = nn.Sequential(
                nn.Linear(in_features=2 * inplanes, out_features=2 * self.mid_channel),
                self.relu,
            )
            self.theta_channel.apply(weights_init_kaiming)

            self.channel_para_1 = nn.Sequential(
                nn.Linear(in_features=self.mid_channel, out_features=int(inplanes)),
                nn.Sigmoid(),
            )
            self.channel_para_1.apply(weights_init_kaiming)

    def forward(self, featmap, re_featmap, vect_featmap, embed_feat):

        b, t, c, h, w = featmap.size()

        if self.is_mutual_spatial_attention == 'yes':
            gamma_feat = self.gamma_temporal(re_featmap).view(b, t, -1, h * w)
            beta_feat = self.beta_temporal(re_featmap).view(b, t, -1, h * w)

        if self.is_mutual_channel_attention == 'yes':
            vect_featmap = vect_featmap.permute(0, 2, 1)

        gap_feat_map0 = []

        for idx in range(0, t, 2):

            channel_para = torch.cat((vect_featmap[:, :, idx], vect_featmap[:, :, idx+1]), 1)
            channel_para = self.theta_channel(channel_para)
            para_00 = self.channel_para_1(channel_para[:, :int(self.inplanes/16)]).view(b, -1, 1, 1)
            para_01 = self.channel_para_1(channel_para[:, int(self.inplanes/16):]).view(b, -1, 1, 1)

            embed_feat0 = embed_feat[:, idx, :, :, :]
            embed_feat1 = embed_feat[:, idx + 1, :, :, :]

            gamma_feat0 = gamma_feat[:, idx, :, :].permute(0, 2, 1)
            beta_feat0 = beta_feat[:, idx + 1, :, :]
            Gs0 = torch.matmul(gamma_feat0, beta_feat0)
            Gs_in0 = Gs0.permute(0, 2, 1).view(b, h * w, h, w)  # hang_0
            Gs_out0 = Gs0.view(b, h * w, h, w)                  # lie_1

            gamma_feat1 = gamma_feat[:, idx + 1, :, :].permute(0, 2, 1)
            beta_feat1 = beta_feat[:, idx, :, :]
            Gs1 = torch.matmul(gamma_feat1, beta_feat1)
            Gs_in1 = Gs1.permute(0, 2, 1).view(b, h * w, h, w)  # hang_0
            Gs_out1 = Gs1.view(b, h * w, h, w)                  # lie_1

            Gs_joint = torch.cat((Gs_in0, Gs_in1, Gs_out0, Gs_out1), 1)
            Gs_joint = self.gg_temporal(Gs_joint)

            para_alpha = self.te_para(torch.cat((embed_feat0, Gs_joint), 1))
            para_beta = self.te_para(torch.cat((embed_feat1, Gs_joint), 1))

            para_00 = para_00 * para_alpha
            para_01 = para_01 * para_beta

            gap_map0 = para_00 * featmap[:, idx, :, :, :] + para_01 * featmap[:, idx + 1, :, :, :]
            # gap_map0 = self.relu(gap_map0)
            gap_map0 = gap_map0 ** 2
            gap_feat_map0.append(gap_map0)

        gap_feat_map0 = torch.stack(gap_feat_map0, 1)
        torch.cuda.empty_cache()

        return gap_feat_map0