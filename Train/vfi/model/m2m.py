#!/usr/bin/env python

import torch

import vfi.model.backwarp as backwarp
import vfi.model.pwcnet as pwcnet
import vfi.model.softsplat as softsplat


##########################################################

def forwarp_mframe_mask(tenIn1, tenFlow1, t1, tenIn2, tenFlow2, t2, tenMetric1=None, tenMetric2=None):

    def one_fdir(tenIn, tenFlow, td, tenMetric):
        tenIn = torch.cat([tenIn * td*(tenMetric).clip(-20.0, 20.0).exp(), td*(tenMetric).clip(-20.0, 20.0).exp()], 1)

        tenOut = softsplat.softsplat_func.apply(tenIn, tenFlow)

        return tenOut[:, :-1, :, :], tenOut[:, -1:, :, :] + 0.0000001
    #end

    flow_num = tenFlow1.shape[0]
    tenOut = 0
    tenNormalize = 0
    for idx in range(flow_num):
        tenOutF, tenNormalizeF = one_fdir(tenIn1[idx], tenFlow1[idx], t1[idx], tenMetric1[idx])
        tenOutB, tenNormalizeB = one_fdir(tenIn2[idx], tenFlow2[idx], t2[idx], tenMetric2[idx])

        tenOut += tenOutF+tenOutB
        tenNormalize += tenNormalizeF+tenNormalizeB
    #end
    
    return tenOut/tenNormalize, tenNormalize<0.00001
#end

###################################################################

c = 16

def conv(in_planes, out_planes, kernel_size=3, stride=1, padding=1, dilation=1):
    return torch.nn.Sequential(
        torch.nn.Conv2d(in_planes, out_planes, kernel_size=kernel_size, stride=stride,
                  padding=padding, dilation=dilation, bias=True),
        torch.nn.PReLU(out_planes)
    )
#end


def deconv(in_planes, out_planes, kernel_size=4, stride=2, padding=1):
    return torch.nn.Sequential(
        torch.torch.nn.ConvTranspose2d(in_channels=in_planes, out_channels=out_planes,
                                 kernel_size=4, stride=2, padding=1, bias=True),
        torch.nn.PReLU(out_planes)
    )
#end


class Conv2(torch.nn.Module):
    def __init__(self, in_planes, out_planes, stride=2):
        super(Conv2, self).__init__()
        self.conv1 = conv(in_planes, out_planes, 3, stride, 1)
        self.conv2 = conv(out_planes, out_planes, 3, 1, 1)
    #end

    def forward(self, x):
        x = self.conv1(x)
        x = self.conv2(x)
        return x
    #end
#end


class Conv2n(torch.nn.Module):
    def __init__(self, in_planes, out_planes, stride=2):
        super(Conv2n, self).__init__()
        self.conv1 = conv(in_planes, in_planes, 3, stride, 1)
        self.conv2 = conv(in_planes, in_planes, 3, 1, 1)
        self.conv3 = conv(in_planes, in_planes, 1, 1, 0)
        self.conv4 = conv(in_planes, out_planes, 1, 1, 0)
    #end

    def forward(self, x):
        x = self.conv1(x)
        x = self.conv2(x)
        x = self.conv3(x)
        x = self.conv4(x)
        return x
    #end
#end

#####################################################

class ImgPyramid(torch.nn.Module):
    def __init__(self):
        super(ImgPyramid, self).__init__()
        self.conv1 = Conv2(3, c)
        self.conv2 = Conv2(c, 2*c)
        self.conv3 = Conv2(2*c, 4*c)
        self.conv4 = Conv2(4*c, 8*c)
    #end

    def forward(self, x):
        x1 = self.conv1(x)
        x2 = self.conv2(x1)
        x3 = self.conv3(x2)
        x4 = self.conv4(x3)
        return [x1, x2, x3, x4]
    #end
#end


class EncDec(torch.nn.Module):
    def __init__(self,branch):
        super(EncDec, self).__init__()
        self.branch = branch

        self.down0 = Conv2(8, 2*c)
        self.down1 = Conv2(6*c, 4*c)
        self.down2 = Conv2(12*c, 8*c)
        self.down3 = Conv2(24*c, 16*c)

        self.up0 = deconv(48*c, 8*c)
        self.up1 = deconv(16*c, 4*c)
        self.up2 = deconv(8*c, 2*c)
        self.up3 = deconv(4*c, c)
        self.conv = torch.nn.Conv2d(c, 2*self.branch, 3, 1, 1)
    
        self.conv_m = torch.nn.Conv2d(c, 1, 3, 1, 1)

        # For Channel dimennsion
        self.conv_C = torch.nn.Sequential(
                            torch.nn.AdaptiveAvgPool2d(1),
                            torch.nn.Conv2d(16*c, 16*16*c, kernel_size=(1,1), stride=(1,1),padding=(0,0), bias=True),
                            torch.nn.Sigmoid()
                            )


        # For Height dimennsion
        self.conv_H = torch.nn.Sequential(
                            torch.nn.AdaptiveAvgPool2d((None,1)),
                            torch.nn.Conv2d(16*c, 16, kernel_size=(1,1), stride=(1,1),padding=(0,0), bias=True),
                            torch.nn.Sigmoid()
                            )



        # For Width dimennsion
        self.conv_W = torch.nn.Sequential(
                            torch.nn.AdaptiveAvgPool2d((1,None)),
                            torch.nn.Conv2d(16*c, 16, kernel_size=(1,1), stride=(1,1),padding=(0,0), bias=True),
                            torch.nn.Sigmoid()
                            )


        self.sigmoid = torch.nn.Sigmoid()
    #end

    def forward(self, flow0, flow1, im0, im1, c0, c1):

        N_,C_,H_,W_ = im0.shape

        wim1 = backwarp.backwarp(im1, flow0)
        wim0 = backwarp.backwarp(im0, flow1)
        s0_0 = self.down0(torch.cat((flow0, im0, wim1), 1))
        s1_0 = self.down0(torch.cat((flow1, im1, wim0), 1))

        #########################################################################################
        flow0 = torch.nn.functional.interpolate(flow0, scale_factor=0.5, mode="bilinear", align_corners=False) * 0.5
        flow1 = torch.nn.functional.interpolate(flow1, scale_factor=0.5, mode="bilinear", align_corners=False) * 0.5

        wf0 = backwarp.backwarp(torch.cat((s0_0, c0[0]),1), flow1)
        wf1 = backwarp.backwarp(torch.cat((s1_0, c1[0]),1), flow0)

        s0_1 = self.down1(torch.cat((s0_0, c0[0], wf1), 1))
        s1_1 = self.down1(torch.cat((s1_0, c1[0], wf0), 1))


        #########################################################################################
        flow0 = torch.nn.functional.interpolate(flow0, scale_factor=0.5, mode="bilinear", align_corners=False) * 0.5
        flow1 = torch.nn.functional.interpolate(flow1, scale_factor=0.5, mode="bilinear", align_corners=False) * 0.5

        wf0 = backwarp.backwarp(torch.cat((s0_1, c0[1]),1), flow1)
        wf1 = backwarp.backwarp(torch.cat((s1_1, c1[1]),1), flow0)

        s0_2 = self.down2(torch.cat((s0_1, c0[1], wf1), 1))
        s1_2 = self.down2(torch.cat((s1_1, c1[1], wf0), 1))


        #########################################################################################
        flow0 = torch.nn.functional.interpolate(flow0, scale_factor=0.5, mode="bilinear", align_corners=False) * 0.5
        flow1 = torch.nn.functional.interpolate(flow1, scale_factor=0.5, mode="bilinear", align_corners=False) * 0.5

        wf0 = backwarp.backwarp(torch.cat((s0_2, c0[2]),1), flow1)
        wf1 = backwarp.backwarp(torch.cat((s1_2, c1[2]),1), flow0)

        s0_3 = self.down3(torch.cat((s0_2, c0[2], wf1), 1))
        s1_3 = self.down3(torch.cat((s1_2, c1[2], wf0), 1))

        #########################################################################################


        s0_3_c = self.conv_C(s0_3)
        s0_3_c = s0_3_c.view(N_,16,-1,1,1)
                        
        s0_3_h = self.conv_H(s0_3)
        s0_3_h = s0_3_h.view(N_,16,1,-1,1)

        s0_3_w = self.conv_W(s0_3)
        s0_3_w = s0_3_w.view(N_,16,1,1,-1)

        cube0 = (s0_3_c*s0_3_h*s0_3_w).mean(1)

        s0_3 = s0_3*cube0

        s1_3_c = self.conv_C(s1_3)
        s1_3_c = s1_3_c.view(N_,16,-1,1,1)
                        
        s1_3_h = self.conv_H(s1_3)
        s1_3_h = s1_3_h.view(N_,16,1,-1,1)

        s1_3_w = self.conv_W(s1_3)
        s1_3_w = s1_3_w.view(N_,16,1,1,-1)

        cube1 = (s1_3_c*s1_3_h*s1_3_w).mean(1)

        s1_3 = s1_3*cube1


        #########################################################################################
        flow0 = torch.nn.functional.interpolate(flow0, scale_factor=0.5, mode="bilinear", align_corners=False) * 0.5
        flow1 = torch.nn.functional.interpolate(flow1, scale_factor=0.5, mode="bilinear", align_corners=False) * 0.5

        wf0 = backwarp.backwarp(torch.cat((s0_3, c0[3]),1), flow1)
        wf1 = backwarp.backwarp(torch.cat((s1_3, c1[3]),1), flow0)

        x0 = self.up0(torch.cat((s0_3, c0[3], wf1), 1))
        x1 = self.up0(torch.cat((s1_3, c1[3], wf0), 1))


        x0 = self.up1(torch.cat((s0_2, x0), 1))
        x1 = self.up1(torch.cat((s1_2, x1), 1))

        x0 = self.up2(torch.cat((s0_1, x0), 1))
        x1 = self.up2(torch.cat((s1_1, x1), 1))

        x0 = self.up3(torch.cat((s0_0, x0), 1))
        x1 = self.up3(torch.cat((s1_0, x1), 1))


        m0 = self.sigmoid(self.conv_m(x0))*0.8+0.1
        m1 = self.sigmoid(self.conv_m(x1))*0.8+0.1

        x0 = self.conv(x0)
        x1 = self.conv(x1)

        return x0, x1, m0.repeat(1,self.branch,1,1), m1.repeat(1,self.branch,1,1)
    #end
#end


class M2M_PWC(torch.nn.Module):
    def __init__(self,ratio=4, branch=4):
        super(M2M_PWC, self).__init__()
        self.branch = branch
        self.ratio = ratio

        self.netFlow = pwcnet.Network()

        self.paramAlpha = torch.nn.Parameter(10.0 * torch.ones(1, 1, 1, 1))

        class MotionRefineNet(torch.nn.Module):
            def __init__(self,branch):
                super(MotionRefineNet, self).__init__()
                self.branch = branch
                self.img_pyramid = ImgPyramid()
                self.motion_encdec = EncDec(branch)
            #end

            def forward(self, flow0, flow1, im0, im1, ratio):

                flow0 = ratio * torch.nn.functional.interpolate(input=flow0, scale_factor=ratio, mode='bilinear', align_corners=False)
                flow1 = ratio * torch.nn.functional.interpolate(input=flow1, scale_factor=ratio, mode='bilinear', align_corners=False)

                c0 = self.img_pyramid(im0)
                c1 = self.img_pyramid(im1)

                flow_res = self.motion_encdec(flow0, flow1, im0, im1, c0, c1)

                flow0 = flow0.repeat(1,self.branch,1,1) + flow_res[0]
                flow1 = flow1.repeat(1,self.branch,1,1) + flow_res[1]

                return flow0, flow1, flow_res[2], flow_res[3]
            #end
        #end

        self.MRN = MotionRefineNet(self.branch)
    #end

    def get_params(self, lr):
        self.lr = lr
        self.tenParams = [
            {'params': self.MRN.parameters(), 'lr': self.lr * 1.0},
            {'params': self.paramAlpha, 'lr': self.lr * 1.0},
            {'params': self.netFlow.netOne.parameters(), 'lr': self.lr * 0.0},
            {'params': self.netFlow.netTwo.parameters(), 'lr': self.lr * 0.0},
            {'params': self.netFlow.netThr.parameters(), 'lr': self.lr * 0.0},
            {'params': self.netFlow.netFou.parameters(), 'lr': self.lr * 0.0},
            {'params': self.netFlow.netFiv.parameters(), 'lr': self.lr * 0.0},
            {'params': self.netFlow.netExtractor.parameters(), 'lr': self.lr * 0.0}
            ]
        return self.tenParams
    #end

    def forward(self, im0, im1, fltTimes=[0.5], ratio=None):
        if ratio is None:
            ratio = self.ratio
        #end

        intWidth = im0.shape[3] and im1.shape[3]
        intHeight = im0.shape[2] and im1.shape[2]

        intPadr = ((ratio * 16) - (intWidth % (ratio * 16))) % (ratio * 16)
        intPadb = ((ratio * 16) - (intHeight % (ratio * 16))) % (ratio * 16)

        im0 = torch.nn.functional.pad(input=im0, pad=[0, intPadr, 0, intPadb], mode='replicate')
        im1 = torch.nn.functional.pad(input=im1, pad=[0, intPadr, 0, intPadb], mode='replicate')

        N_,C_,H_,W_ = im0.shape

        outputs = []

        with torch.set_grad_enabled(False):
            tenStats = [im0, im1]
            tenMean_ = sum([tenIn.mean([1, 2, 3], True) for tenIn in tenStats]) / len(tenStats)
            tenStd_ = (sum([tenIn.std([1, 2, 3], False, True).square() + (tenMean_ - tenIn.mean([1, 2, 3], True)).square() for tenIn in tenStats]) / len(tenStats)).sqrt()

            im0_o = (im0 - tenMean_) / (tenStd_ + 0.0000001)
            im1_o = (im1 - tenMean_) / (tenStd_ + 0.0000001)

            im0 = (im0 - tenMean_) / (tenStd_ + 0.0000001)
            im1 = (im1 - tenMean_) / (tenStd_ + 0.0000001)
        #end

        im0_ = torch.nn.functional.interpolate(input=im0, scale_factor=2.0/ratio, mode='bilinear', align_corners=False)
        im1_ = torch.nn.functional.interpolate(input=im1, scale_factor=2.0/ratio, mode='bilinear', align_corners=False)

        tenFwd, tenBwd = self.netFlow.bidir(im0_, im1_)

        tenFwd,tenBwd, WeiMF, WeiMB = self.MRN(tenFwd, tenBwd, im0, im1, ratio)


        for fltTime_ in fltTimes:

            im0 = im0_o.repeat(1,self.branch,1,1)
            im1 = im1_o.repeat(1,self.branch,1,1)
            tenStd = tenStd_.repeat(1,self.branch,1,1)
            tenMean = tenMean_.repeat(1,self.branch,1,1)
            fltTime = fltTime_.repeat(1,self.branch,1,1)


            tenFwd = tenFwd.reshape(N_, self.branch, 2, H_, W_).view(N_*self.branch, 2, H_, W_)
            tenBwd = tenBwd.reshape(N_, self.branch, 2, H_, W_).view(N_*self.branch, 2, H_, W_)

            WeiMF = WeiMF.reshape(N_, self.branch, 1, H_, W_).view(N_*self.branch, 1, H_, W_)
            WeiMB = WeiMB.reshape(N_, self.branch, 1, H_, W_).view(N_*self.branch, 1, H_, W_)

            im0 = im0.reshape(N_, self.branch, 3, H_, W_).view(N_*self.branch, 3, H_, W_)
            im1 = im1.reshape(N_, self.branch, 3, H_, W_).view(N_*self.branch, 3, H_, W_)

            tenStd = tenStd.reshape(N_,self.branch,1,1,1).view(N_*self.branch, 1, 1, 1)
            tenMean = tenMean.reshape(N_,self.branch,1,1,1).view(N_*self.branch, 1, 1, 1)
            fltTime = fltTime.reshape(N_,self.branch,1,1,1).view(N_*self.branch, 1, 1, 1)

            tenPhotoone = (1.0 - (WeiMF * (im0 - backwarp.backwarp(im1, tenFwd).detach()).abs().mean([1], True))).clip(0.001, None).square()
            tenPhototwo = (1.0 - (WeiMB * (im1 - backwarp.backwarp(im0, tenBwd).detach()).abs().mean([1], True))).clip(0.001, None).square()


            t0 = fltTime
            flow0 = tenFwd * t0 
            metric0 = self.paramAlpha * tenPhotoone

            t1 = 1.0 - fltTime
            flow1 = tenBwd * t1
            metric1 = self.paramAlpha * tenPhototwo

            flow0 = flow0.reshape(N_, self.branch, 2, H_, W_).permute(1,0,2,3,4)
            flow1 = flow1.reshape(N_, self.branch, 2, H_, W_).permute(1,0,2,3,4)

            metric0 = metric0.reshape(N_, self.branch, 1, H_, W_).permute(1,0,2,3,4)
            metric1 = metric1.reshape(N_, self.branch, 1, H_, W_).permute(1,0,2,3,4)

            im0 = im0.reshape(N_, self.branch, 3, H_, W_).permute(1,0,2,3,4)
            im1 = im1.reshape(N_, self.branch, 3, H_, W_).permute(1,0,2,3,4)

            t0 = t0.reshape(N_, self.branch, 1, 1, 1).permute(1,0,2,3,4)
            t1 = t1.reshape(N_, self.branch, 1, 1, 1).permute(1,0,2,3,4)

            tenOutput, mask = forwarp_mframe_mask(im0, flow0, t1, im1, flow1, t0, metric0, metric1)

            tenOutput = tenOutput+mask*(t1.mean(0)*im0_o+t0.mean(0)*im1_o)

            outputs.append((tenOutput* ( tenStd_ + 0.0000001)) + tenMean_)
        #end

        return [output[:, :, :intHeight, :intWidth] for output in outputs]
    #end
#end

