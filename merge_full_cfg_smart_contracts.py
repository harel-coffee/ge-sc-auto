import os
import json

from os.path import join
from copy import deepcopy
from tqdm import tqdm

import networkx as nx
from slither.slither import Slither
from slither.core.cfg.node import NodeType

def get_node_info(node, list_vulnerabilities_info_in_sc):
    node_label = "Node Type: {}\n".format(str(node.type))
    node_type = str(node.type)
    if node.expression:
        node_label += "\nEXPRESSION:\n{}\n".format(node.expression)
        node_expression = str(node.expression)
    else:
        node_expression = None
    if node.irs:
        node_label += "\nIRs:\n" + "\n".join([str(ir) for ir in node.irs])
        node_irs = "\n".join([str(ir) for ir in node.irs])
    else:
        node_irs = None

    node_source_code_lines = node.source_mapping['lines']
    node_info_vulnerabilities = get_vulnerabilities_of_node_by_source_code_line(node_source_code_lines, list_vulnerabilities_info_in_sc)
    
    return node_label, node_type, node_expression, node_irs, node_info_vulnerabilities

def get_vulnerabilities(file_name_sc, vulnerabilities):

    list_vulnerability_in_sc = None
    for vul_item in vulnerabilities:
        if file_name_sc == vul_item['name']:
            list_vulnerability_in_sc = vul_item['vulnerabilities']
            
    return list_vulnerability_in_sc

def get_vulnerabilities_of_node_by_source_code_line(source_code_lines, list_vul_info_sc):
    if list_vul_info_sc is not None:
        list_vulnerability = []
        for vul_info_sc in list_vul_info_sc:
            vulnerabilities_lines = vul_info_sc['lines']
            for source_code_line in source_code_lines:
                for vulnerabilities_line in vulnerabilities_lines:
                    if source_code_line == vulnerabilities_line:
                        list_vulnerability.append(vul_info_sc)
    else:
        list_vulnerability = None
    
    if list_vulnerability is None or len(list_vulnerability) == 0:
        node_info_vulnerabilities = None
    else:
        node_info_vulnerabilities = list_vulnerability

    return node_info_vulnerabilities

def compress_full_smart_contracts(smart_contracts, output, vulnerabilities=None):
    full_graph = None
    for sc in tqdm(smart_contracts):
        file_name_sc = sc.split('/')[-1:][0]
        try:
            slither = Slither(sc)
        except Exception as e:
            print(e)
            continue
        
        if vulnerabilities is not None:
            list_vul_info_sc = get_vulnerabilities(file_name_sc, vulnerabilities)
        else:
            list_vul_info_sc = None
        print(file_name_sc, list_vul_info_sc)

        merge_contract_graph = None
        for contract in slither.contracts:
            merged_graph = None
            for idx, function in enumerate(contract.functions + contract.modifiers):  

                nx_g = nx.MultiDiGraph()
                for nidx, node in enumerate(function.nodes):             
                    node_label, node_type, node_expression, node_irs, node_info_vulnerabilities = get_node_info(node, list_vul_info_sc)
                    
                    nx_g.add_node(node.node_id, label=node_label,
                                  node_type=node_type, node_expression=node_expression, node_irs=node_irs,
                                  node_info_vulnerabilities=node_info_vulnerabilities,
                                  function_fullname=function.full_name, contract_name=contract.name, source_file=file_name_sc)
                    
                    if node.type in [NodeType.IF, NodeType.IFLOOP]:
                        true_node = node.son_true
                        if true_node:
                            if true_node.node_id not in nx_g.nodes():
                                node_label, node_type, node_expression, node_irs, node_info_vulnerabilities = get_node_info(true_node, list_vul_info_sc)
                                nx_g.add_node(true_node.node_id, label=node_label,
                                              node_type=node_type, node_expression=node_expression, node_irs=node_irs,
                                              node_info_vulnerabilities=node_info_vulnerabilities,
                                              function_fullname=function.full_name, contract_name=contract.name, source_file=file_name_sc)
                            nx_g.add_edge(node.node_id, true_node.node_id, edge_type='if_true', label='True')
                        
                        
                        false_node = node.son_false
                        if false_node:
                            if false_node.node_id not in nx_g.nodes():
                                node_label, node_type, node_expression, node_irs, node_info_vulnerabilities = get_node_info(false_node, list_vul_info_sc)
                                nx_g.add_node(false_node.node_id, label=node_label,
                                              node_type=node_type, node_expression=node_expression, node_irs=node_irs,
                                              node_info_vulnerabilities=node_info_vulnerabilities,
                                              function_fullname=function.full_name, contract_name=contract.name, source_file=file_name_sc)
                            nx_g.add_edge(node.node_id, false_node.node_id, edge_type='if_false', label='False')
                            
                    else:
                        for son_node in node.sons:
                            if son_node:
                                if son_node.node_id not in nx_g.nodes():
                                    node_label, node_type, node_expression, node_irs, node_info_vulnerabilities = get_node_info(son_node, list_vul_info_sc)
                                    nx_g.add_node(son_node.node_id, label=node_label,
                                                  node_type=node_type, node_expression=node_expression, node_irs=node_irs,
                                                  node_info_vulnerabilities=node_info_vulnerabilities,
                                                  function_fullname=function.full_name, contract_name=contract.name, source_file=file_name_sc)
                                nx_g.add_edge(node.node_id, son_node.node_id, edge_type='next', label='Next')

                nx_graph = nx_g
                # add FUNCTION_NAME node
                node_function_name = file_name_sc + '_' + contract.name + '_' + function.full_name
                node_function_source_code_lines = function.source_mapping['lines']
                node_function_info_vulnerabilities = get_vulnerabilities_of_node_by_source_code_line(node_function_source_code_lines, list_vul_info_sc)
                nx_graph.add_node(node_function_name, label=node_function_name,
                                  node_type='FUNCTION_NAME', node_expression=None, node_irs=None,
                                  node_info_vulnerabilities=node_function_info_vulnerabilities,
                                  function_fullname=function.full_name, contract_name=contract.name, source_file=file_name_sc)
                
                if 0 in nx_graph.nodes():
                    nx_graph.add_edge(node_function_name, 0, edge_type='next', label='Next')

                nx_graph = nx.relabel_nodes(nx_graph, lambda x: contract.name + '_' + function.full_name + '_' + str(x), copy=False)

                if merged_graph is None:
                    merged_graph = deepcopy(nx_graph)
                else:
                    merged_graph = nx.disjoint_union(merged_graph, nx_graph)

            if merge_contract_graph is None:
                merge_contract_graph = deepcopy(merged_graph)
            elif merged_graph is not None:
                merge_contract_graph = nx.disjoint_union(merge_contract_graph, merged_graph)
        
        if full_graph is None:
            full_graph = deepcopy(merge_contract_graph)
        elif merge_contract_graph is not None:
            full_graph = nx.disjoint_union(full_graph, merge_contract_graph)

    # for node, node_data in full_graph.nodes(data=True):
    #     if node_data['node_info_vulnerabilities'] is not None:
    #         print('Node has vulnerabilities:', node, node_data)

    nx.nx_agraph.write_dot(full_graph, join(output, 'compress_graphs.dot'))
    nx.write_gpickle(full_graph, join(output, 'compress_graphs.gpickle'))


if __name__ == '__main__':
    # smart_contract_path = 'data/extracted_source_code' 
    # output_path = 'data/extracted_source_code'
    smart_contract_path = 'data/smartbug-dataset/reentrancy' 
    output_path = 'data/smartbug-dataset/reentrancy'
    smart_contracts = [join(smart_contract_path, f) for f in os.listdir(smart_contract_path) if f.endswith('.sol')]
    
    data_vulnerabilities = None
    with open('data/smartbug-dataset/vulnerabilities.json') as f:
        data_vulnerabilities = json.load(f)
    
    compress_full_smart_contracts(smart_contracts, output_path, vulnerabilities=data_vulnerabilities)
