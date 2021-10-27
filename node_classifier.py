"""This model shows an example of using dgl.metapath_reachable_graph on the original heterogeneous
graph.

Because the original HAN implementation only gives the preprocessed homogeneous graph, this model
could not reproduce the result in HAN as they did not provide the preprocessing code, and we
constructed another dataset from ACM with a different set of papers, connections, features and
labels.
"""
import os

import torch
import networkx as nx
from sklearn.model_selection import KFold

from sco_models.model_hetero import HANVulClassifier
from sco_models.model_node_classification import HANVulNodeClassifier
from sco_models.utils import score, get_classification_report, get_confusion_matrix, dump_result
from sco_models.visualization import visualize_average_k_folds


def get_binary_mask(total_size, indices):
    mask = torch.zeros(total_size)
    mask[indices] = 1
    return mask.byte()


def get_node_ids(graph, source_files):
    file_ids = []
    for node_ids, node_data in graph.nodes(data=True):
        filename = node_data['source_file']
        if filename in source_files:
            file_ids.append(node_ids)
    return file_ids


def main(args):
    epochs = args['num_epochs']
    k_folds = args['k_folds']
    device = args['device']
    # kfold = KFold(n_splits=k_folds, shuffle=True)
    train_results = {}
    val_results = {}
    # Get feature extractor
    print('Getting features')
    if args['node_feature'] == 'han':
        feature_extractor = HANVulNodeClassifier(args['feature_compressed_graph'], args['dataset'], feature_extractor=args['cfg_feature_extractor'], node_feature='gae', device=args['device'])
        feature_extractor.load_state_dict(torch.load(args['feature_extractor']))
        feature_extractor.to(args['device'])
        feature_extractor.eval()
    else:
        feature_extractor = args['feature_extractor']

    nx_graph = nx.read_gpickle(args['compressed_graph'])
    number_of_nodes = len(nx_graph)
    model = HANVulNodeClassifier(args['compressed_graph'], args['dataset'], feature_extractor=feature_extractor, node_feature=args['node_feature'], device=device)
    total_train_files = [f for f in os.listdir(args['dataset']) if f.endswith('.sol')]
    total_test_files = [f for f in os.listdir(args['testset']) if f.endswith('.sol')]
    total_train_files = list(set(total_train_files).difference(set(total_test_files)))
    clean_smart_contract = '/home/minhnn/minhnn/ICSE/ge-sc/data/smartbugs_wild/clean_50'
    # total_clean_files = [f for f in os.listdir(clean_smart_contract) if f.endswith('.sol')]
    total_clean_files = []
    total_train_files = list(set(total_train_files).difference(set(total_clean_files)))

    # Train valid split data
    train_rate = 0.6
    val_rate = 0.2
    rand_train_ids = torch.randperm(len(total_train_files)).tolist()
    rand_test_ids = torch.randperm(len(total_test_files)).tolist()
    rand_clean_ids = torch.randperm(len(total_clean_files)).tolist()

    train_size_0 = int(train_rate * len(total_train_files))
    train_size_1 = int(train_rate * len(total_test_files))
    train_size_2 = int(train_rate * len(total_clean_files))
    train_files = [total_train_files[i] for i in rand_train_ids[:train_size_0]] + \
                  [total_test_files[i] for i in rand_test_ids[:train_size_1]] + \
                  [total_clean_files[i] for i in rand_clean_ids[:train_size_2]]
    print('Buggy train files: ', [total_train_files[i] for i in rand_train_ids[:train_size_0]])
    print('Curated train files: ', [total_test_files[i] for i in rand_test_ids[:train_size_1]])

    val_size_0 = int(val_rate * len(total_train_files))
    val_size_1 = int(val_rate * len(total_test_files))
    val_size_2 = int(val_rate * len(total_clean_files))
    val_files = [total_train_files[i] for i in rand_train_ids[train_size_0:train_size_0 + val_size_0]] + \
                [total_test_files[i] for i in rand_test_ids[train_size_1:train_size_1 + val_size_1]] + \
                [total_clean_files[i] for i in rand_clean_ids[train_size_2:train_size_2 + val_size_2]]
    print('Buggy valid files: ', [total_train_files[i] for i in rand_train_ids[train_size_0:train_size_0 + val_size_0]])
    print('Curated valid files: ', [total_test_files[i] for i in rand_test_ids[train_size_1:train_size_1 + val_size_1]])
    test_files = [total_train_files[i] for i in rand_train_ids[train_size_0 + val_size_0:]] + \
                 [total_test_files[i] for i in rand_test_ids[train_size_1 + val_size_1:]] + \
                 [total_clean_files[i] for i in rand_clean_ids[train_size_2 + val_size_2:]]
    print('Buggy test files: ', [total_train_files[i] for i in rand_train_ids[train_size_0 + val_size_0:]])
    print('Curated test files: ', [total_test_files[i] for i in rand_test_ids[train_size_1 + val_size_1:]])

    assert len(train_files) + len(val_files) + len(test_files) == len(total_train_files) + len(total_test_files) + len(total_clean_files)

    print('Label dict: ', model.label_ids)
    print(f'Number of source code for Buggy/Curated: {len(total_train_files)}/{len(total_test_files)}')
    total_train_ids = get_node_ids(nx_graph, total_train_files)
    train_ids = get_node_ids(nx_graph, train_files)
    val_ids = get_node_ids(nx_graph, val_files)
    test_ids = get_node_ids(nx_graph, test_files)
    targets = torch.tensor(model.node_labels, device=args['device'])
    assert len(set(train_ids) | set(val_ids) | set(test_ids)) == len(targets)
    buggy_node_ids = torch.nonzero(targets).squeeze().tolist()
    print('Buggy node {}/{} ({}%)'.format(len(set(buggy_node_ids)), len(targets), 100*len(set(buggy_node_ids))/len(targets)))
    # for fold, (train_ids, val_ids) in enumerate(kfold.split(total_train_ids)):
        # Init model 
    fold = 0
    model.reset_parameters()
    model.to(device)
    train_results[fold] = {'loss': [], 'acc': [], 'micro_f1': [], 'macro_f1': [], 'lrs': []}
    val_results[fold] = {'loss': [], 'acc': [], 'micro_f1': [], 'macro_f1': []}
    train_buggy_node_ids = set(buggy_node_ids).intersection(set(train_ids))
    print('Buggy nodes in train: {}/{} ({}%)'.format(len(train_buggy_node_ids), len(train_ids), 100*len(train_buggy_node_ids)/len(train_ids)))
    val_buggy_node_ids = set(buggy_node_ids).intersection(set(val_ids))
    print('Buggy nodes in valid: {}/{} ({}%)'.format(len(val_buggy_node_ids), len(val_ids), 100*len(val_buggy_node_ids)/len(val_ids)))
    test_buggy_node_ids =set(buggy_node_ids).intersection(set(test_ids))
    print('Buggy nodes in test: {}/{} ({}%)'.format(len(test_buggy_node_ids), len(test_ids), 100*len(test_buggy_node_ids)/len(test_ids)))
    print('Start training fold {} with {}/{} train/val smart contracts'.format(fold, len(train_ids), len(val_ids)))
    total_steps = epochs
    # class_counter = [len(labeled_node_ids['valid']), len(labeled_node_ids['buggy'])]
    # class_weight = torch.tensor([1 - sample/len(class_counter) for sample in class_counter], requires_grad=False).to(args['device'])
    # Don't record the following operation in autograd
    # with torch.no_grad():
    #     loss_weights.copy_(initial_weights)
    loss_fcn = torch.nn.CrossEntropyLoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=0.0002)
    scheduler = torch.optim.lr_scheduler.OneCycleLR(optimizer, max_lr=0.005, total_steps=total_steps)
    train_mask = get_binary_mask(number_of_nodes, train_ids)
    val_mask = get_binary_mask(number_of_nodes, val_ids)
    test_mask = get_binary_mask(number_of_nodes, test_ids)
    if hasattr(torch, 'BoolTensor'):
        train_mask = train_mask.bool()
        val_mask = val_mask.bool()
        test_mask = test_mask.bool()
    retain_graph = True if args['node_feature'] == 'han' else False
    for epoch in range(epochs):
        print('Fold {} - Epochs {}'.format(fold, epoch))
        optimizer.zero_grad()
        logits = model()
        logits = logits.to(args['device'])
        train_loss = loss_fcn(logits[train_mask], targets[train_mask]) 
        train_loss.backward(retain_graph=retain_graph)
        optimizer.step()
        scheduler.step()
        train_acc, train_micro_f1, train_macro_f1 = score(targets[train_mask], logits[train_mask])
        print('Train Loss: {:.4f} | Train Micro f1: {:.4f} | Train Macro f1: {:.4f} | Train Accuracy: {:.4f}'.format(
                train_loss.item(), train_micro_f1, train_macro_f1, train_acc))
        val_loss = loss_fcn(logits[val_mask], targets[val_mask]) 
        val_acc, val_micro_f1, val_macro_f1 = score(targets[val_mask], logits[val_mask])
        print('Val Loss:   {:.4f} | Val Micro f1:   {:.4f} | Val Macro f1:   {:.4f} | Val Accuracy:   {:.4f}'.format(
                val_loss.item(), val_micro_f1, val_macro_f1, val_acc))

        train_results[fold]['loss'].append(train_loss)
        train_results[fold]['micro_f1'].append(train_micro_f1)
        train_results[fold]['macro_f1'].append(train_macro_f1)
        train_results[fold]['acc'].append(train_acc)
        train_results[fold]['lrs'] += scheduler.get_last_lr()

        val_results[fold]['loss'].append(val_loss)
        val_results[fold]['micro_f1'].append(val_micro_f1)
        val_results[fold]['macro_f1'].append(val_macro_f1)
        val_results[fold]['acc'].append(val_acc)
    print('Saving model fold {}'.format(fold))
    dump_result(targets[val_mask], logits[val_mask], os.path.join(args['output_models'], f'confusion_{fold}.csv'))
    save_path = os.path.join(args['output_models'], f'han_fold_{fold}.pth')
    torch.save(model.state_dict(), save_path)
    print('Testing phase')
    print(f'Testing on {len(test_ids)} nodes')
    model.eval()
    with torch.no_grad():
        logits = model()
        logits = logits.to(args['device'])
        test_acc, test_micro_f1, test_macro_f1 = score(targets[test_mask], logits[test_mask])
        print('Test Micro f1:   {:.4f} | Test Macro f1:   {:.4f} | Test Accuracy:   {:.4f}'.format(test_micro_f1, test_macro_f1, test_acc))
        print('Classification report', '\n', get_classification_report(targets[test_mask], logits[test_mask]))
        print('Confusion matrix', '\n', get_confusion_matrix(targets[test_mask], logits[test_mask]))
    return train_results, val_results


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser('HAN')
    parser.add_argument('-s', '--seed', type=int, default=1,
                        help='Random seed')
    parser.add_argument('-ld', '--log-dir', type=str, default='./logs/ijcai2020_smartbugs', help='Dir for saving training results')
    parser.add_argument('--compressed_graph', type=str, default='./dataset/call_graph/compressed_graph/compress_call_graphs_no_solidity_calls.gpickle')
    parser.add_argument('--dataset', type=str, default='./dataset/aggregate/source_code')
    parser.add_argument('--test_compressed_graph', type=str, default='./dataset/smartbugs/compressed_graphs/compress_graphs.gpickle')
    parser.add_argument('--testset', type=str, default='./dataset/smartbugs/source_code')
    parser.add_argument('--label', type=str, default='./dataset/aggregate/labels.json')
    parser.add_argument('--output_models', type=str, default='./models/call_graph_rgcn')
    parser.add_argument('--checkpoint', type=str, default='./models/call_graph_rgcn/han_fold_1.pth')
    parser.add_argument('--feature_compressed_graph', type=str, default='./dataset/aggregate/compressed_graph/compressed_graphs.gpickle')
    parser.add_argument('--feature_extractor', type=str, default='./models/metapath2vec_cfg/han_fold_1.pth')
    parser.add_argument('--cfg_feature_extractor', type=str, default='./models/metapath2vec_cfg/han_fold_1.pth')
    parser.add_argument('--node_feature', type=str, default='metapath2vec')
    parser.add_argument('--k_folds', type=int, default=1)
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
        nx_graph = nx.read_gpickle(args['compressed_graph'])
        number_of_nodes = len(nx_graph)
        test_files = [f for f in os.listdir(args['testset']) if f.endswith('.sol')]
        model = HANVulNodeClassifier(args['compressed_graph'], args['dataset'], feature_extractor=None, node_feature=args['node_feature'], device=args['device'])
        model.load_state_dict(torch.load(args['checkpoint']))
        model.eval()
        model.to(args['device'])
        test_ids = get_node_ids(nx_graph, test_files)
        targets = torch.tensor(model.node_labels, device=args['device'])
        buggy_node_ids = torch.nonzero(targets).squeeze().tolist()
        test_buggy_node_ids = set(buggy_node_ids) & set(test_ids)
        print('Buggy nodes in test: {}/{} ({}%)'.format(len(test_buggy_node_ids), len(test_ids), 100*len(test_buggy_node_ids)/len(test_ids)))
        test_mask = get_binary_mask(number_of_nodes, test_ids)
        if hasattr(torch, 'BoolTensor'):
            test_mask = test_mask.bool()
        print(f"Testing on {len(test_ids)} nodes")
        with torch.no_grad():
            logits = model()
            logits = logits.to(args['device'])
            print(torch.nonzero(targets, as_tuple=True)[0].shape)
            test_acc, test_micro_f1, test_macro_f1 = score(targets[test_mask], logits[test_mask])
            print('Test Micro f1:   {:.4f} | Test Macro f1:   {:.4f} | Test Accuracy:   {:.4f}'.format(test_micro_f1, test_macro_f1, test_acc))
            print('Classification report', '\n', get_classification_report(targets[test_mask], logits[test_mask]))
            print('Confusion matrix', '\n', get_confusion_matrix(targets[test_mask], logits[test_mask]))
