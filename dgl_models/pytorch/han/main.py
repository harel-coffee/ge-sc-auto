import os
from sys import abiflags

import torch
from torch.nn.functional import cross_entropy
from sklearn.metrics import f1_score, precision_recall_fscore_support
from sklearn.model_selection import KFold
from dgl.dataloading import GraphDataLoader
from torch.utils.tensorboard import SummaryWriter

from dataloader import EthIdsDataset
from model_hetero import HAN, HANVulClassifier
from visualization import visualize_average_k_folds


def score(logits, labels):
    _, indices = torch.max(logits, dim=1)
    prediction = indices.long().cpu().numpy()
    labels = labels.cpu().numpy()
    accuracy = (prediction == labels).sum() / len(prediction)
    micro_f1 = f1_score(labels, prediction, average='micro')
    macro_f1 = f1_score(labels, prediction, average='macro')
    return accuracy, micro_f1, macro_f1


def accuracy(preds, labels):
    return (preds == labels).sum().item() / labels.shape[0]


def train(args, model, train_loader, optimizer, loss_fcn, epoch):
    model.train()
    total_accucracy =  0
    total_macro_f1 = 0
    total_micro_f1 = 0
    total_loss = 0
    circle_lrs = []
    for idx, (batched_graph, labels) in enumerate(train_loader):
        labels = labels.to(args['device'])
        optimizer.zero_grad()
        logits, _ = model(batched_graph)
        loss = loss_fcn(logits, labels)
        train_acc, train_micro_f1, train_macro_f1 = score(logits, labels)
        loss.backward()
        optimizer.step()
        total_accucracy += train_acc
        total_micro_f1 += train_micro_f1
        total_macro_f1 += train_macro_f1
        total_loss += loss.item()
        circle_lrs.append(optimizer.param_groups[0]["lr"])
    steps = idx + 1
    return total_loss/steps, total_micro_f1/steps, train_macro_f1/steps, total_accucracy/steps, circle_lrs


def validate(args, model, val_loader, loss_fcn):
    model.eval()
    total_loss = 0
    total_macro_f1 = 0
    total_micro_f1 = 0
    total_accucracy =  0
    with torch.no_grad():
        for idx, (batched_graph, labels) in enumerate(val_loader):
            labels = labels.to(args['device'])
            logits, _ = model(batched_graph)
            loss = loss_fcn(logits, labels)
            total_loss += loss.item()
            val_acc, val_micro_f1, val_macro_f1 = score(logits, labels)
            total_accucracy += val_acc
            total_micro_f1 += val_micro_f1
            total_macro_f1 += val_macro_f1
    steps = idx + 1
    return total_loss/steps, total_micro_f1/steps, val_macro_f1/steps, total_accucracy/steps


def test(args, model, test_loader):
    model.eval()
    total_macro_f1 = 0
    total_micro_f1 = 0
    total_accucracy =  0
    with torch.no_grad():
        for idx, (batched_graph, labels) in enumerate(test_loader):
            labels = labels.to(args['device'])
            logits = model(batched_graph)
            test_acc, test_micro_f1, test_macro_f1 = score(logits, labels)
            total_accucracy += test_acc
            total_micro_f1 += test_micro_f1
            total_macro_f1 += test_macro_f1
    steps = idx + 1
    return total_micro_f1/steps, test_macro_f1/steps, total_accucracy/steps



def main(args):
    epochs = args['num_epochs']
    k_folds = args['k_folds']
    device = args['device']
    ethdataset = EthIdsDataset(args['dataset'], args['compressed_graph'], args['label'])
    kfold = KFold(n_splits=k_folds, shuffle=True)
    train_results = {}
    val_results = {}
    # Get feature extractor
    print('Getting features')
    if args['node_feature'] == 'han':
        han_model = HANVulClassifier(args['feature_compressed_graph'], ethdataset.filename_mapping, node_feature='nodetype', hidden_size=16, device=args['device'])
        han_model.load_state_dict(torch.load(args['feature_extractor']))
        han_model.to(args['device'])
        han_model.eval()
    else:
        han_model = None

    for fold, (train_ids, val_ids) in enumerate(kfold.split(range(ethdataset.num_graphs))):
        train_results[fold] = {'loss': [], 'acc': [], 'micro_f1': [], 'macro_f1': [], 'lrs': []}
        val_results[fold] = {'loss': [], 'acc': [], 'micro_f1': [], 'macro_f1': []}
        train_subsampler = torch.utils.data.SubsetRandomSampler(train_ids)
        val_subsampler = torch.utils.data.SubsetRandomSampler(val_ids)
        train_dataloader = GraphDataLoader(ethdataset,batch_size=args['batch_size'],drop_last=False,sampler=train_subsampler)
        val_dataloader = GraphDataLoader(ethdataset,batch_size=args['batch_size'],drop_last=False,sampler=val_subsampler)
        print('Start training fold {} with {}/{} train/val smart contracts'.format(fold, len(train_subsampler), len(val_subsampler)))
        total_steps = epochs
        model = HANVulClassifier(args['compressed_graph'], ethdataset.filename_mapping, feature_extractor=han_model, node_feature=args['node_feature'], device=device)
        model.to(device)
        loss_fcn = torch.nn.CrossEntropyLoss()
        optimizer = torch.optim.Adam(model.parameters(), lr=0.0005)
        scheduler = torch.optim.lr_scheduler.OneCycleLR(optimizer, max_lr=0.01, total_steps=total_steps)
        lrs = []
        for epoch in range(epochs):
            print('Fold {} - Epochs {}'.format(fold, epoch))
            train_loss, train_micro_f1, train_macro_f1, train_acc, lrs = train(args, model, train_dataloader, optimizer, loss_fcn, epoch)
            print('Train Loss: {:.4f} | Train Micro f1: {:.4f} | Train Macro f1: {:.4f} | Train Accuracy: {:.4f}'.format(
                    train_loss, train_micro_f1, train_macro_f1, train_acc))
            val_loss, val_micro_f1, val_macro_f1, val_acc = validate(args, model, val_dataloader, loss_fcn)
            print('Val Loss:   {:.4f} | Val Micro f1:   {:.4f} | Val Macro f1:   {:.4f} | Val Accuracy:   {:.4f}'.format(
                    val_loss, val_micro_f1, val_macro_f1, val_acc))
            scheduler.step()
            train_results[fold]['loss'].append(train_loss)
            train_results[fold]['micro_f1'].append(train_micro_f1)
            train_results[fold]['macro_f1'].append(train_macro_f1)
            train_results[fold]['acc'].append(train_acc)
            train_results[fold]['lrs'] += lrs

            val_results[fold]['loss'].append(val_loss)
            val_results[fold]['micro_f1'].append(val_micro_f1)
            val_results[fold]['macro_f1'].append(val_macro_f1)
            val_results[fold]['acc'].append(val_acc)

        print('Saving model fold {}'.format(fold))
        save_path = os.path.join(args['output_models'], f'han_fold_{fold}.pth')
        torch.save(model.state_dict(), save_path)
    return train_results, val_results


def load_model(model_path):
    model = HANVulClassifier()
    model.load_state_dict(torch.load(model_path))
    return model.eval()


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser('HAN')
    parser.add_argument('-s', '--seed', type=int, default=1,
                        help='Random seed')
    parser.add_argument('-ld', '--log-dir', type=str, default='./logs/ijcai2020_smartbugs', help='Dir for saving training results')
    parser.add_argument('--compressed_graph', type=str, default='./dataset/call_graph/compressed_graph/compress_call_graphs_no_solidity_calls.gpickle')
    parser.add_argument('--dataset', type=str, default='./dataset/aggregate/source_code')
    parser.add_argument('--testset', type=str, default='./dataset/smartbugs/source_code')
    parser.add_argument('--label', type=str, default='./dataset/aggregate/labels.json')
    parser.add_argument('--output_models', type=str, default='./models/call_graph')
    parser.add_argument('--checkpoint', type=str, default='./models/ijcai2020_smartbugs/han_fold_1.pth')
    parser.add_argument('--feature_compressed_graph', type=str, default='./dataset/aggregate/compressed_graph/compressed_graphs.gpickle')
    parser.add_argument('--feature_extractor', type=str, default='./models/metapath2vec_cfg/han_fold_1.pth')
    parser.add_argument('--node_feature', type=str, default='metapath2vec')
    parser.add_argument('--k_folds', type=int, default=5)
    parser.add_argument('--test', action='store_true')
    parser.add_argument('--non_visualize', action='store_true')
    args = parser.parse_args().__dict__

    default_configure = {
    'lr': 0.0005,             # Learning rate
    'num_heads': 8,        # Number of attention heads for node-level attention
    'hidden_units': 8,
    'dropout': 0.6,
    'weight_decay': 0.001,
    'num_epochs': 100,
    'batch_size': 256,
    'patience': 100,
    'device': 'cuda:0' if torch.cuda.is_available() else 'cpu'
    }
    args.update(default_configure)
    torch.manual_seed(args['seed'])

    if not os.path.exists(args['output_models']):
        os.makedirs(args['output_models'])

    # Training
    if not args['test']:
        print('Training phase')
        train_results, val_results = main(args)
        if not args['non_visualize']:
            print('Visualizing')
            visualize_average_k_folds(args, train_results, val_results)
    # Testing
    else:
        print('Testing phase')
        ethdataset = EthIdsDataset(args['dataset'], args['compressed_graph'], args['label'])
        smartbugs_ids = [ethdataset.filename_mapping[sc] for sc in os.listdir(args['testset'])]
        test_dataloader = GraphDataLoader(ethdataset, batch_size=8, drop_last=False, sampler=smartbugs_ids)
        model = HANVulClassifier(args['compressed_graph'], ethdataset.filename_mapping, hidden_size=16, out_size=2,num_heads=8, dropout=0.6, device=args['device'])
        model.load_state_dict(torch.load(args['checkpoint']))
        model.to(args['device'])
        test_micro_f1, test_macro_f1, test_acc = test(args, model, test_dataloader)
        print('Test Micro f1:   {:.4f} | Test Macro f1:   {:.4f} | Test Accuracy:   {:.4f}'.format(test_micro_f1, test_macro_f1, test_acc))
