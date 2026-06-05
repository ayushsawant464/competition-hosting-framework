"""AST transformer to instrument user code for cycle counting, stack tracking, and memory tracing."""
import ast
from typing import Optional, List, Set

def _get_base_name(node: ast.AST) -> Optional[str]:
    """Recursively trace a chain of attribute/subscript/call loads to find the base variable name."""
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        return _get_base_name(node.value)
    elif isinstance(node, ast.Subscript):
        return _get_base_name(node.value)
    elif isinstance(node, ast.Call):
        return _get_base_name(node.func)
    return None

def _get_all_base_names(node: ast.AST) -> List[str]:
    """Retrieve all distinct base variable names from a target expression (handles unpacking)."""
    names: List[str] = []
    if isinstance(node, (ast.Tuple, ast.List)):
        for elt in node.elts:
            names.extend(_get_all_base_names(elt))
    else:
        name = _get_base_name(node)
        if name:
            names.append(name)
    # Deduplicate and return
    return list(set(names))

class MutatedNameFinder(ast.NodeVisitor):
    """Finds names that might be mutated via method calls (e.g., list.append)."""
    def __init__(self) -> None:
        self.names: Set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute):
            base = _get_base_name(node.func.value)
            if base:
                self.names.add(base)
        self.generic_visit(node)

def _count_locals(node: ast.FunctionDef) -> int:
    """Estimates the number of local variables in a function definition."""
    locals_set: Set[str] = set()
    
    # Include parameters
    for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
        locals_set.add(arg.arg)
    if node.args.vararg:
        locals_set.add(node.args.vararg.arg)
    if node.args.kwarg:
        locals_set.add(node.args.kwarg.arg)
        
    # Include variables assigned in the function body
    class LocalFinder(ast.NodeVisitor):
        def visit_Name(self, n: ast.Name) -> None:
            if isinstance(n.ctx, ast.Store):
                locals_set.add(n.id)
        # Avoid recursing into nested function/class scopes
        def visit_FunctionDef(self, n: ast.FunctionDef) -> None:
            pass
        def visit_AsyncFunctionDef(self, n: ast.AsyncFunctionDef) -> None:
            pass
        def visit_ClassDef(self, n: ast.ClassDef) -> None:
            pass

    for item in node.body:
        LocalFinder().visit(item)
        
    return len(locals_set)

class StatementCostCalculator(ast.NodeVisitor):
    """Calculates the total virtual CPU cycle cost of all expressions within a statement."""
    def __init__(self, cost_table: dict, local_vars: Set[str]) -> None:
        self.costs = cost_table
        self.local_vars = local_vars
        self.total_cost = 0

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            if node.id in self.local_vars:
                self.total_cost += self.costs.get('NameLocal', 1)
            else:
                self.total_cost += self.costs.get('NameGlobal', 8)
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        self.total_cost += self.costs.get('BinOp', 2)
        self.generic_visit(node)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> None:
        self.total_cost += self.costs.get('UnaryOp', 1)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        n_comparisons = len(node.ops)
        self.total_cost += n_comparisons * self.costs.get('Compare', 2)
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        n_ops = len(node.values) - 1
        self.total_cost += n_ops * self.costs.get('BoolOp', 1)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        self.total_cost += self.costs.get('Subscript', 3)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.total_cost += self.costs.get('Attribute', 4)
        self.generic_visit(node)

    def visit_Starred(self, node: ast.Starred) -> None:
        self.total_cost += self.costs.get('Starred', 2)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        self.total_cost += self.costs.get('Call', 2)
        self.generic_visit(node)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        self.total_cost += self.costs.get('JoinedStr', 8)
        self.generic_visit(node)

    def visit_FormattedValue(self, node: ast.FormattedValue) -> None:
        self.total_cost += self.costs.get('FormattedValue', 3)
        self.generic_visit(node)

    # Do not recurse into nested declarations/statements or comprehensions
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass
    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        pass
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        pass
    def visit_ListComp(self, node: ast.ListComp) -> None:
        self.total_cost += self.costs.get('ListComp', 3)
        for gen in node.generators:
            self.visit(gen.iter)
    def visit_SetComp(self, node: ast.SetComp) -> None:
        self.total_cost += self.costs.get('SetComp', 4)
        for gen in node.generators:
            self.visit(gen.iter)
    def visit_DictComp(self, node: ast.DictComp) -> None:
        self.total_cost += self.costs.get('DictComp', 5)
        for gen in node.generators:
            self.visit(gen.iter)
    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self.total_cost += self.costs.get('GeneratorExp', 1)
        for gen in node.generators:
            self.visit(gen.iter)

class EmulationTransformer(ast.NodeTransformer):
    """
    Transforms AST to inject operation counters, call frame pushes/pops,
    and memory tracking hooks into user code.
    """
    def __init__(self) -> None:
        super().__init__()
        self.current_locals: Set[str] = set()
        self.costs = {
            # Control Flow
            'For': 4,            # Balanced iterator step cost
            'While': 3,
            'If': 2,
            'FunctionDef': 3,    # Frame creation overhead
            'Return': 1,         # Frame teardown
            'Try': 5,
            'ExceptHandler': 10,
            
            # Data Operations
            'Assign': 2,
            'TupleAssign': 6,     # Tuple packing/unpacking overhead
            'AugAssign': 3,
            'BinOp': 2,
            'UnaryOp': 1,
            'Compare': 2,
            'BoolOp': 1,
            
            # Data Access / Expression elements
            'Subscript': 2,
            'Attribute': 2,
            'Starred': 2,
            'Call': 2,           # Method/Function dispatch overhead
            'NameLocal': 1,      # LOAD_FAST
            'NameGlobal': 4,     # LOAD_GLOBAL with cache optimization
            
            # Comprehensions
            'ListComp': 3,
            'DictComp': 5,
            'SetComp': 4,
            'GeneratorExp': 1,
            
            # String formatting
            'JoinedStr': 8,
            'FormattedValue': 3
        }

    def _make_increment_call(self, cost: int) -> ast.Expr:
        """Creates an AST node for: __emu__.increment(cost)"""
        return ast.Expr(
            value=ast.Call(
                func=ast.Attribute(
                    value=ast.Name(id='__emu__', ctx=ast.Load()),
                    attr='increment',
                    ctx=ast.Load()
                ),
                args=[ast.Constant(value=cost)],
                keywords=[]
            )
        )

    def _make_track_mem_call(self, name: str) -> ast.Expr:
        """Creates an AST node for: __emu__.track_mem('name', name)"""
        return ast.Expr(
            value=ast.Call(
                func=ast.Attribute(
                    value=ast.Name(id='__emu__', ctx=ast.Load()),
                    attr='track_mem',
                    ctx=ast.Load()
                ),
                args=[ast.Constant(value=name), ast.Name(id=name, ctx=ast.Load())],
                keywords=[]
            )
        )

    def _make_free_call(self, name: str) -> ast.Expr:
        """Creates an AST node for: __emu__.free('name')"""
        return ast.Expr(
            value=ast.Call(
                func=ast.Attribute(
                    value=ast.Name(id='__emu__', ctx=ast.Load()),
                    attr='free',
                    ctx=ast.Load()
                ),
                args=[ast.Constant(value=name)],
                keywords=[]
            )
        )

    def _make_clear_anon_call(self) -> ast.Expr:
        """Creates an AST node for: __emu__.clear_anonymous()"""
        return ast.Expr(
            value=ast.Call(
                func=ast.Attribute(
                    value=ast.Name(id='__emu__', ctx=ast.Load()),
                    attr='clear_anonymous',
                    ctx=ast.Load()
                ),
                args=[],
                keywords=[]
            )
        )

    def _transform_statement_list(self, statements: List[ast.stmt]) -> List[ast.stmt]:
        """Process a list of statements, injecting cycle tracking and memory hooks."""
        new_statements: List[ast.stmt] = []
        for stmt in statements:
            # 1. Recursively visit child nodes first (e.g. nested blocks, comprehensions, calls)
            visited_stmt = self.visit(stmt)
            if visited_stmt is None:
                continue
            
            if isinstance(visited_stmt, list):
                # If a visitor returned a list of statements, they are already processed
                new_statements.extend(visited_stmt)
                continue

            # 2. Calculate the cost of expressions within this statement
            calc = StatementCostCalculator(self.costs, self.current_locals)
            calc.visit(visited_stmt)
            
            # Determine base statement cost
            base_cost = 0
            if isinstance(visited_stmt, ast.Assign):
                # Check for tuple/list unpacking
                if any(isinstance(t, (ast.Tuple, ast.List)) for t in visited_stmt.targets):
                    base_cost = self.costs.get('TupleAssign', 30)
                else:
                    base_cost = self.costs.get('Assign', 2)
            elif isinstance(visited_stmt, ast.AugAssign):
                base_cost = self.costs.get('AugAssign', 3)
            elif isinstance(visited_stmt, ast.Delete):
                base_cost = self.costs.get('Assign', 2)
            elif isinstance(visited_stmt, ast.Return):
                base_cost = self.costs.get('Return', 1)
            elif isinstance(visited_stmt, ast.If):
                base_cost = self.costs.get('If', 2)
            elif isinstance(visited_stmt, ast.While):
                base_cost = self.costs.get('While', 3)
            elif isinstance(visited_stmt, ast.For):
                base_cost = self.costs.get('For', 15)
            elif isinstance(visited_stmt, ast.Try):
                base_cost = self.costs.get('Try', 5)
            elif isinstance(visited_stmt, ast.ExceptHandler):
                base_cost = self.costs.get('ExceptHandler', 10)
                
            total_cost = calc.total_cost + base_cost
            
            # 3. Add increment call if cycles were spent
            if total_cost > 0:
                new_statements.append(self._make_increment_call(total_cost))
                
            # 4. Add the statement itself
            new_statements.append(visited_stmt)
            
            # 5. Add post-statement memory hooks
            if isinstance(visited_stmt, ast.Assign):
                for target in visited_stmt.targets:
                    for name in _get_all_base_names(target):
                        new_statements.append(self._make_track_mem_call(name))
            elif isinstance(visited_stmt, ast.AugAssign):
                for name in _get_all_base_names(visited_stmt.target):
                    new_statements.append(self._make_track_mem_call(name))
            elif isinstance(visited_stmt, ast.Delete):
                for target in visited_stmt.targets:
                    for name in _get_all_base_names(target):
                        new_statements.append(self._make_free_call(name))
            elif isinstance(visited_stmt, ast.Expr):
                mut_finder = MutatedNameFinder()
                mut_finder.visit(visited_stmt.value)
                for name in mut_finder.names:
                    new_statements.append(self._make_track_mem_call(name))

            # 6. Clear temporary anonymous memory at statement boundary
            new_statements.append(self._make_clear_anon_call())

        return new_statements

    def visit_Module(self, node: ast.Module) -> ast.Module:
        node.body = self._transform_statement_list(node.body)
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        # Calculate local variable count and local names
        locals_set = set()
        for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
            locals_set.add(arg.arg)
        if node.args.vararg:
            locals_set.add(node.args.vararg.arg)
        if node.args.kwarg:
            locals_set.add(node.args.kwarg.arg)
            
        class LocalFinder(ast.NodeVisitor):
            def visit_Name(self, n: ast.Name) -> None:
                if isinstance(n.ctx, ast.Store):
                    locals_set.add(n.id)
            def visit_FunctionDef(self, n: ast.FunctionDef) -> None:
                pass
            def visit_AsyncFunctionDef(self, n: ast.AsyncFunctionDef) -> None:
                pass
            def visit_ClassDef(self, n: ast.ClassDef) -> None:
                pass

        for item in node.body:
            LocalFinder().visit(item)
            
        n_locals = len(locals_set)
        
        # Save previous local scope, load new function local scope
        old_locals = self.current_locals
        self.current_locals = locals_set
        
        # Transform the function body
        instrumented_body = self._transform_statement_list(node.body)
        
        # Restore previous scope
        self.current_locals = old_locals
        
        # Create stack push statement
        push_call = ast.Expr(
            value=ast.Call(
                func=ast.Attribute(
                    value=ast.Name(id='__emu__', ctx=ast.Load()),
                    attr='push_frame',
                    ctx=ast.Load()
                ),
                args=[ast.Constant(value=node.name), ast.Constant(value=n_locals)],
                keywords=[]
            )
        )
        
        # Create stack pop statement
        pop_call = ast.Expr(
            value=ast.Call(
                func=ast.Attribute(
                    value=ast.Name(id='__emu__', ctx=ast.Load()),
                    attr='pop_frame',
                    ctx=ast.Load()
                ),
                args=[],
                keywords=[]
            )
        )
        
        # Wrap function body in try...finally to ensure stack frames are always popped
        try_finally = ast.Try(
            body=instrumented_body,
            handlers=[],
            orelse=[],
            finalbody=[pop_call]
        )
        
        node.body = [push_call, try_finally]
        return node

    def visit_If(self, node: ast.If) -> ast.If:
        node.body = self._transform_statement_list(node.body)
        node.orelse = self._transform_statement_list(node.orelse)
        return node

    def visit_While(self, node: ast.While) -> ast.While:
        node.body = self._transform_statement_list(node.body)
        
        # Charge condition cost at the start of loop body for subsequent checks
        calc = StatementCostCalculator(self.costs, self.current_locals)
        calc.visit(node.test)
        loop_test_cost = calc.total_cost + self.costs.get('While', 3)
        if loop_test_cost > 0:
            node.body.insert(0, self._make_increment_call(loop_test_cost))
            
        return node

    def visit_For(self, node: ast.For) -> ast.For:
        node.body = self._transform_statement_list(node.body)
        node.orelse = self._transform_statement_list(node.orelse)
        
        # Charge iteration step + loop target assignment cost inside the body
        step_cost = self.costs.get('For', 15) + self.costs.get('Assign', 2)
        if step_cost > 0:
            node.body.insert(0, self._make_increment_call(step_cost))
            
        return node

    def visit_Try(self, node: ast.Try) -> ast.Try:
        node.body = self._transform_statement_list(node.body)
        node.handlers = [self.visit(h) for h in node.handlers]
        node.orelse = self._transform_statement_list(node.orelse)
        node.finalbody = self._transform_statement_list(node.finalbody)
        return node

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> ast.ExceptHandler:
        node.body = self._transform_statement_list(node.body)
        return node

    def visit_ListComp(self, node: ast.ListComp) -> ast.ListComp:
        self.generic_visit(node)
        node.elt = ast.Subscript(
            value=ast.Tuple(
                elts=[
                    self._make_increment_call(3).value,
                    node.elt
                ],
                ctx=ast.Load()
            ),
            slice=ast.Constant(value=1),
            ctx=ast.Load()
        )
        return node

    def visit_SetComp(self, node: ast.SetComp) -> ast.SetComp:
        self.generic_visit(node)
        node.elt = ast.Subscript(
            value=ast.Tuple(
                elts=[
                    self._make_increment_call(4).value,
                    node.elt
                ],
                ctx=ast.Load()
            ),
            slice=ast.Constant(value=1),
            ctx=ast.Load()
        )
        return node

    def visit_DictComp(self, node: ast.DictComp) -> ast.DictComp:
        self.generic_visit(node)
        node.value = ast.Subscript(
            value=ast.Tuple(
                elts=[
                    self._make_increment_call(5).value,
                    node.value
                ],
                ctx=ast.Load()
            ),
            slice=ast.Constant(value=1),
            ctx=ast.Load()
        )
        return node

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> ast.GeneratorExp:
        self.generic_visit(node)
        node.elt = ast.Subscript(
            value=ast.Tuple(
                elts=[
                    self._make_increment_call(1).value,
                    node.elt
                ],
                ctx=ast.Load()
            ),
            slice=ast.Constant(value=1),
            ctx=ast.Load()
        )
        return node

    def visit_Call(self, node: ast.Call) -> ast.AST:
        # Check for method calls (e.g. obj.method(...))
        if isinstance(node.func, ast.Attribute):
            obj = node.func.value
            method_name = node.func.attr
            
            # Recursively transform children first
            obj = self.visit(obj)
            args = [self.visit(arg) for arg in node.args]
            keywords = [self.visit(kw) for kw in node.keywords]
            
            # Rewrite to: __emu__.call_method(obj, 'method_name', *args, **keywords)
            new_node = ast.Call(
                func=ast.Attribute(
                    value=ast.Name(id='__emu__', ctx=ast.Load()),
                    attr='call_method',
                    ctx=ast.Load()
                ),
                args=[
                    obj,
                    ast.Constant(value=method_name)
                ] + args,
                keywords=keywords
            )
            return new_node
            
        return self.generic_visit(node)

def instrument_code(source_code: str) -> str:
    """Parse, transform, and unparse Python source code."""
    import textwrap
    source_code = textwrap.dedent(source_code).strip()
    tree = ast.parse(source_code)
    transformer = EmulationTransformer()
    transformed_tree = transformer.visit(tree)
    ast.fix_missing_locations(transformed_tree)
    return ast.unparse(transformed_tree)
