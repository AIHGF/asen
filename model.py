import torch
import torch.nn as nn
import numpy as np
import torch.nn.functional as F
from torch.nn import init

def l2norm(X):
    """L2-normalize columns of X
    """
    norm = torch.pow(X, 2).sum(dim=1, keepdim=True).sqrt()
    X = torch.div(X, norm)
    return X


class Tripletnet(nn.Module):
    def __init__(self, embeddingnet):
        super(Tripletnet, self).__init__()
        self.embeddingnet = embeddingnet

    def forward(self, x, y, z, c):
        """ x: Anchor image,
            y: Distant (negative) image,
            z: Close (positive) image,
            c: Integer indicating according to which attribute images are compared"""
        embedded_x = self.embeddingnet(x, c)
        embedded_y = self.embeddingnet(y, c)
        embedded_z = self.embeddingnet(z, c)
        sim_a = torch.sum(embedded_x * embedded_y, dim=1)
        sim_b = torch.sum(embedded_x * embedded_z, dim=1)

        return sim_a, sim_b


class ASENet(nn.Module):
    def __init__(self, backbonenet, embedding_size, n_attributes):
        super(ASENet, self).__init__()
        self.backbonenet = backbonenet
        self.n_attributes = n_attributes
        self.embedding_size = embedding_size

        self.mask_fc1 = nn.Linear(self.n_attributes, 512, bias=False)
        self.mask_fc2 = nn.Linear(self.n_attributes, 1024, bias=False)
        self.fc1 = nn.Linear(2048, 512)
        self.fc2 = nn.Linear(512, 1024)
        self.feature_fc = nn.Linear(1024, 1024)
        self.conv1 = nn.Conv2d(1024, 512, kernel_size=1, stride=1)
        self.conv2 = nn.Conv2d(512, 1, kernel_size=1, stride=1)
        self.tanh = nn.Tanh()
        self.relu = nn.ReLU()
        self.softmax = nn.Softmax(dim=2)
        self.sigmoid = nn.Sigmoid()
        
    def forward(self, x, task, norm=True):
        x = self.backbonenet(x)

        img_embedding = self.conv1(x)
        img_embedding = self.tanh(img_embedding)

        c = task.view(task.size(0), 1).cpu()
        mask_fc_input = torch.zeros(c.size(0), self.n_attributes).scatter_(1, c, 1)
        mask_fc_input = mask_fc_input.cuda()
        mask = self.mask_fc1(mask_fc_input)
        mask = self.tanh(mask)
        mask = mask.view(mask.size(0), mask.size(1), 1, 1)
        mask = mask.expand(mask.size(0), mask.size(1), 14, 14)

        #spatial attention
        attmap = mask * img_embedding
        attmap = self.conv2(attmap)
        attmap = self.tanh(attmap)
        attmap = attmap.view(attmap.size(0), attmap.size(1), -1)
        attmap = self.softmax(attmap)
        attmap = attmap.view(attmap.size(0), attmap.size(1), 14, 14)

        x = x * attmap
        x = x.view(x.size(0), x.size(1), x.size(2)*x.size(3))
        x = x.sum(dim=2)

        #channel attention
        mask = self.relu(self.mask_fc2(mask_fc_input))
        mask = torch.cat((x, mask), dim=1)
        mask = self.fc1(mask)
        mask = self.relu(mask)
        mask = self.fc2(mask)
        mask = self.sigmoid(mask)
        x = x * mask
        x = self.feature_fc(x)

        if norm:
            x = l2norm(x)

        return x

    def get_heatmaps(self, x, task):
        feature = self.backbonenet(x)

        img_embedding = self.conv1(feature)
        img_embedding = self.tanh(img_embedding)

        task = task.view(task.size(0), 1).cpu()
        mask_fc_input = torch.zeros(task.size(0), self.n_attributes).scatter_(1, task, 1)
        mask_fc_input = mask_fc_input.cuda()
        mask = self.mask_fc1(mask_fc_input)
        mask = self.tanh(mask)
        mask = mask.view(mask.size(0), mask.size(1), 1, 1)
        mask = mask.expand(mask.size(0), mask.size(1), 14, 14)

        attmap = mask * img_embedding
        attmap = self.conv2(attmap)
        attmap = self.tanh(attmap)
        attmap = attmap.view(attmap.size(0), attmap.size(1), -1)
        attmap = self.softmax(attmap)
        attmap = attmap.view(attmap.size(0), attmap.size(1), 14, 14)
        attmap = attmap.squeeze()
        return attmap


class ASENet_V2(nn.Module):
    def __init__(self, backbonenet, embedding_size, n_attributes):
        super(ASENet_V2, self).__init__()
        self.backbonenet = backbonenet
        self.n_attributes = n_attributes
        self.embedding_size = embedding_size

        self.attr_embedding = torch.nn.Embedding(n_attributes, 512)

        self.attr_transform1 = nn.Linear(512, 512)
        self.conv1 = nn.Conv2d(1024, 512, kernel_size=1, stride=1)
        self.img_bn1 = nn.BatchNorm2d(512)

        self.attr_transform2 = nn.Linear(512, 512)
        self.fc1 = nn.Linear(1536, 512)
        self.fc2 = nn.Linear(512, 1024)

        self.feature_fc = nn.Linear(1024, self.embedding_size)

        self.tanh = nn.Tanh()
        self.relu = nn.ReLU(inplace=True)
        self.softmax = nn.Softmax(dim=2)
        self.sigmoid = nn.Sigmoid()
        
    def forward(self, x, c, norm=True):
        x = self.backbonenet(x)

        img = self.conv1(x)
        img = self.img_bn1(img)
        img = self.tanh(img)

        attr = self.attr_embedding(c)
        attr = self.attr_transform1(attr)
        attr = self.tanh(attr)
        attr = attr.view(attr.size(0), attr.size(1), 1, 1)
        attr = attr.expand(attr.size(0), attr.size(1), 14, 14)

        #attribute-aware spatial attention
        attmap = attr * img
        attmap = torch.sum(attmap, dim=1, keepdim=True)
        attmap = torch.div(attmap, 512 ** 0.5)
        attmap = attmap.view(attmap.size(0), attmap.size(1), -1)
        attmap = self.softmax(attmap)
        attmap = attmap.view(attmap.size(0), attmap.size(1), 14, 14)

        x = x * attmap
        x = x.view(x.size(0), x.size(1), x.size(2)*x.size(3))
        x = x.sum(dim=2)

        #channel attention
        attr = self.attr_embedding(c)
        attr = self.attr_transform2(attr)
        attr = self.relu(attr)
        img_attr = torch.cat((x, attr), dim=1)
        mask = self.fc1(img_attr)
        mask = self.relu(mask)
        mask = self.fc2(mask)
        mask = self.sigmoid(mask)
        x = x * mask

        x = self.feature_fc(x)

        if norm:
            x = l2norm(x)

        return x

    def get_heatmaps(self, x, c):
        x = self.backbonenet(x)

        img = self.conv1(x)
        img = self.img_bn1(img)
        img = self.tanh(img)

        attr = self.attr_embedding(c)
        attr = self.attr_transform1(attr)
        attr = self.tanh(attr)
        attr = attr.view(attr.size(0), attr.size(1), 1, 1)
        attr = attr.expand(attr.size(0), attr.size(1), 14, 14)

        #attribute-aware spatial attention
        attmap = attr * img
        attmap = torch.sum(attmap, dim=1, keepdim=True)
        attmap = torch.div(attmap, 512 ** 0.5)
        attmap = attmap.view(attmap.size(0), attmap.size(1), -1)
        attmap = self.softmax(attmap)
        attmap = attmap.view(attmap.size(0), attmap.size(1), 14, 14)

        attmap = attmap.squeeze()

        return attmap


model_dict = {
    'Tripletnet': Tripletnet,
    'ASENet': ASENet,
    'ASENet_V2': ASENet_V2
}
def get_model(name):
    return model_dict[name]