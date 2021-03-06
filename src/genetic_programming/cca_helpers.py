import numpy as np
from numpy import log
from sklearn.linear_model import LogisticRegression
from data.dataset_loader import TextCollectionLoader
import cPickle as pickle
from utils.helpers import create_if_not_exists
import os
from scipy.sparse.linalg import norm
from scipy.sparse import issparse, csr_matrix
from joblib import Parallel
from joblib import delayed
import numpy as np
from sklearn.metrics import f1_score
from cca_operations import *
from cca_terminals import *

# ----------------------------------------------------------------
# Collection Loader
# ----------------------------------------------------------------
def loadCollection(dataset, pos_cat, fs, data_home='../genetic_home'):
    version = TextCollectionLoader.version
    create_if_not_exists(data_home)
    pickle_name = '-'.join(map(str,[dataset,pos_cat,fs,version]))+'.pkl'
    pickle_path = os.path.join(data_home, pickle_name)
    if not os.path.exists(pickle_path):
        data = TextCollectionLoader(dataset=dataset, vectorizer='count', rep_mode='sparse', positive_cat=pos_cat,feat_sel=fs)
        Xtr, ytr = data.get_train_set()
        Xva, yva = data.get_validation_set()
        Xte, yte = data.get_test_set()
        pickle.dump((Xtr,ytr,Xva,yva,Xte,yte), open(pickle_path,'wb'), pickle.HIGHEST_PROTOCOL)
    else:
        Xtr, ytr, Xva, yva, Xte, yte = pickle.load(open(pickle_path,'rb'))
    Xtr=Xtr.asfptype()
    Xva=Xva.asfptype()
    Xte=Xte.asfptype()
    return Xtr,ytr,Xva,yva,Xte,yte


# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------
unary_function_list = [logarithm]
binary_function_list = [addition, multiplication, division]
param_free_terminals = [ft01_csr, ft02_csr, ft03_csr, ft04_csr, ft05_csr, ft06_row, ft07_row, ft08_row, ft09_row, ft10_row,
                 ft11_row, ft12_col, ft13_col, ft14_col, ft18_csr, ct21_float]
parametriced_terminals = [ft15_col, ft16_col, ft17_col]
terminal_list = param_free_terminals + parametriced_terminals

constant_list = map(float, range(100))

class Operation:
    def __init__(self, operation, nargs):
        assert hasattr(operation, '__name__'), 'anonymous operation '
        assert hasattr(operation, '__call__'), '{} is not callable'.format(operation.__name__)
        self.operation = operation
        self.nargs = nargs

    def __call__(self, *args, **kwargs):
        assert len(args)==self.nargs, 'wrong number of arguments'
        return self.operation(*args)

    def __str__(self):
        return self.operation.__name__

class Terminal:
    def __init__(self, name, terminal):
        self.name = name
        self.terminal = terminal

    def __str__(self):
        if self.isconstant():
            return str(self.terminal)
        else:
            return self.name

    def isconstant(self):
        return self.name == ct21_float.__name__


class Tree:
    MAX_TREE_DEPTH = 15
    def __init__(self, node=None):
        assert isinstance(node, Operation) or isinstance(node, Terminal), 'invalid node'
        self.node = node
        self.fitness_score = None
        if isinstance(self.node, Operation):
            self.branches = [None] * self.node.nargs
        else:
            self.branches = []

    def add(self, *trees):
        assert [isinstance(t, Tree) for t in trees], 'unexpected type'
        assert len(trees) == len(self.branches), 'wrong number of branches'
        for i,tree in enumerate(trees):
            self.branches[i]=tree

    def depth(self):
        if isinstance(self.node, Terminal):
            return 1
        else:
            return 1+max(branch.depth() for branch in self.branches)

    def __str__(self):
        return self.__tostr(0) + '[depth={}]'.format(self.depth())

    def __tostr(self, tabs=0):
        _tabs = '\t'*tabs
        if isinstance(self.node, Terminal):
            return _tabs+str(self.node)
        return _tabs+'(' + str(self.node) +'\n'+ '\n'.join([b.__tostr(tabs+1) for b in self.branches]) + '\n'+_tabs+')'


    def __call__(self, eval_dict=None):
        """
        :param eval_dict: a dictionary of terminal-name:terminal, to be used (if passed) when evaluating the tree. This is
         useful to, e.g., evaluate the tree on the validation or test set
        :return:
        """
        if isinstance(self.node, Terminal):
            if eval_dict is None or self.node.isconstant(): #constants are to be taken from the tree
                return self.node.terminal
            else:
                return eval_dict[self.node.name].terminal
        else:
            args = [t(eval_dict) for t in self.branches]
            return self.node(*args)

    def fitness(self, eval_dict, ytr, yva):
        if self.fitness_score is None:
            if Tree.MAX_TREE_DEPTH is not None and self.depth() > Tree.MAX_TREE_DEPTH:
                self.fitness_score = 0
            else:
                try:
                    Xtr_w = self()
                    if isinstance(Xtr_w, float) or min(Xtr_w.shape) == 1: #non valid element, either a float, a row-vector or a colum-vector, not a full matrix
                        self.fitness_score = 0
                    else:
                        Xva_w = self(eval_dict)
                        logreg = LogisticRegression()
                        logreg.fit(Xtr_w, ytr)
                        yva_ = logreg.predict(Xva_w)
                        self.fitness_score = f1_score(y_true=yva, y_pred=yva_, average='binary', pos_label=1)
                except Exception: # some individuals may generate Inf or too large values
                    self.fitness_score = 0
        return self.fitness_score

    def preorder(self):
        import itertools
        return [self] + list(itertools.chain.from_iterable([branch.preorder() for branch in self.branches]))

    def random_branch(self):
        branches = self.preorder()
        if len(branches)>1:
            branches = branches[1:]
        return np.random.choice(branches)

    def copy(self):
        tree = Tree(self.node)
        if self.branches:
            tree.add(*[branch.copy() for branch in self.branches])
        tree.fitness_score = self.fitness_score
        return tree


def get_operations():
    return [Operation(f, 1) for f in unary_function_list] + [Operation(f, 2) for f in binary_function_list]


def get_terminals(X, slope_t15=None, slope_t16=None, slope_t17=None, asdict=False):
    terminals = [Terminal(f.__name__, f(X)) for f in param_free_terminals]
    if slope_t15: terminals.append(Terminal(ft15_col.__name__, ft15_col(X, slope_t15)))
    if slope_t16: terminals.append(Terminal(ft16_col.__name__, ft16_col(X, slope_t16)))
    if slope_t17: terminals.append(Terminal(ft17_col.__name__, ft17_col(X, slope_t17)))

    if asdict:
        terminals = {t.name:t for t in terminals}

    return terminals


def random_tree(max_length, exact_length, operation_pool, terminal_pool):
    """
    :param max_length: the maximun length of a branch
    :param exact_length: if True, forces all branches to have exactly max_length, if otherwise, a branch is only constrained
    to have <= max_length
    :return: a population
    """

    def __random_tree(length, exact_length, first_term=None):
        if first_term is None:
            term = np.random.choice(terminal_pool)
            if term.isconstant():  # the terminal is the constant function
                term = Terminal(term.name, term.terminal())  # which has to be instantiated to obtain the constant value
        else:
            term = first_term
        t = Tree(term)
        while t.depth() < length:
            op = np.random.choice(operation_pool)
            father = Tree(op)
            if op.nargs == 1:
                father.add(t)
            elif op.nargs == 2:
                branch_length = t.depth() if exact_length else np.random.randint(length - t.depth())
                branch = __random_tree(branch_length, exact_length)
                if np.random.rand() < 0.5:
                    father.add(t, branch)
                else:
                    father.add(branch, t)
            t = father
        return t

    first_term = np.random.choice(terminal_pool[:5]) # guarantee a full matrix is in the tree
    length = max_length if exact_length else np.random.randint(1, max_length+1)
    return __random_tree(length, exact_length, first_term)


def ramped_half_and_half_method(n, max_depth, operation_pool, terminal_pool):
    half_exact = [random_tree(max_depth, True, operation_pool, terminal_pool) for _ in range(n//2)]
    half_randlength = [random_tree(max_depth, False, operation_pool, terminal_pool) for _ in range(n//2)]
    return half_exact+half_randlength

def fitness_wrap(individual, ter_validation, ytr, yva):
    individual.fitness(ter_validation, ytr, yva)

def fitness_population(population, ter_validation, ytr, yva, n_jobs=-1, show=False):
    Parallel(n_jobs=n_jobs, backend="threading")(delayed(fitness_wrap)(ind,ter_validation, ytr, yva) for ind in population)
    sort_by_fitness(population)
    best = population[0]
    if show:
        for i, p in enumerate(population):
             print("{}: fitness={:.3f} [depth={}]".format(i, p.fitness_score, p.depth()))
        best_score = population[0].fitness_score
        print('Best individual score: {:.3f}'.format(best_score))
        print(best)
    return best

def sort_by_fitness(sorted_population):
    sorted_population.sort(key=lambda x: x.fitness_score, reverse=True)

def reproduction(population, rate_r=0.05):
    sort_by_fitness(population)
    totake = int(len(population) * rate_r)
    return population[:totake]

def mutate(population, operation_pool, terminal_pool, rate_m=0.05):
    length = int(len(population) * rate_m)
    mutated = [mutation(x, operation_pool, terminal_pool) for x in np.random.choice(population, length, replace=True)]
    return mutated

def mutation(x, operation_pool, terminal_pool):
    mutated = x.copy()
    old_branch = mutated.random_branch()
    mut_branch = random_tree(old_branch.depth(), False, operation_pool, terminal_pool)

    old_branch.node = mut_branch.node
    old_branch.branches = mut_branch.branches
    mutated.fitness_score = None

    return mutated

def cross(x,y):
    x_child = x.copy()
    y_child = y.copy()

    b1 = x_child.random_branch()
    b2 = y_child.random_branch()

    #swap attributes
    b1.node, b2.node = b2.node, b1.node
    b1.branches, b2.branches = b2.branches, b1.branches
    x_child.fitness_score, y_child.fitness_core = None, None

    return x_child, y_child


def crossover(population, rate_c=0.9, k=6): #tournament selection
    def tournament():
        group = np.random.choice(population, size=k, replace=True).tolist()
        group.sort(key=lambda x: x.fitness_score, reverse=True)
        return group[0]

    length = int(len(population)*rate_c)
    new_population = []
    while len(new_population) < length:
        parent1 = tournament()
        parent2 = tournament()
        child1, child2 = cross(parent1, parent2)
        new_population.append(child1)
        new_population.append(child2)

    return new_population

