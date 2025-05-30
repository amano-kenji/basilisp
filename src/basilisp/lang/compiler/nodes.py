from abc import ABC, abstractmethod
from collections.abc import Iterable, MutableMapping, Sequence
from enum import Enum
from typing import Any, Callable, Generic, Optional, TypeVar, Union

import attr

from basilisp.lang import keyword as kw
from basilisp.lang import map as lmap
from basilisp.lang import queue as lqueue
from basilisp.lang import set as lset
from basilisp.lang import symbol as sym
from basilisp.lang import vector as vec
from basilisp.lang.interfaces import (
    IPersistentMap,
    IPersistentSet,
    IPersistentVector,
    ISeq,
)
from basilisp.lang.runtime import Namespace, Var, to_lisp
from basilisp.lang.typing import LispForm
from basilisp.lang.typing import ReaderForm as ReaderLispForm
from basilisp.lang.typing import SpecialForm
from basilisp.lang.util import munge

ARITIES = kw.keyword("arities")
BODY = kw.keyword("body")
CLASS = kw.keyword("class")
LOCAL = kw.keyword("local")
STATEMENTS = kw.keyword("statements")
RET = kw.keyword("ret")
CLASS_LOCAL = kw.keyword("class-local")
THIS_LOCAL = kw.keyword("this-local")
FIELDS = kw.keyword("fields")
MEMBERS = kw.keyword("members")
PARAMS = kw.keyword("params")
TARGET = kw.keyword("target")
VAL = kw.keyword("val")
ARGS = kw.keyword("args")
FN = kw.keyword("fn")
BINDINGS = kw.keyword("bindings")
KEYS = kw.keyword("keys")
VALS = kw.keyword("vals")
EXPR = kw.keyword("expr")
EXPRS = kw.keyword("exprs")
ITEMS = kw.keyword("items")
EXCEPTION = kw.keyword("exception")
META = kw.keyword("meta")
TEST = kw.keyword("test")
THEN = kw.keyword("then")
ELSE = kw.keyword("else")


class NodeOp(Enum):
    AWAIT = kw.keyword("await")
    BINDING = kw.keyword("binding")
    CATCH = kw.keyword("catch")
    CONST = kw.keyword("const")
    DEF = kw.keyword("def")
    DEFTYPE = kw.keyword("deftype")
    DEFTYPE_PROPERTY = kw.keyword("deftype-property")
    DEFTYPE_METHOD = kw.keyword("deftype-method")
    DEFTYPE_METHOD_ARITY = kw.keyword("deftype-method-arity")
    DEFTYPE_CLASSMETHOD = kw.keyword("deftype-classmethod")
    DEFTYPE_STATICMETHOD = kw.keyword("deftype-staticmethod")
    DO = kw.keyword("do")
    FN = kw.keyword("fn")
    FN_ARITY = kw.keyword("fn-arity")
    HOST_CALL = kw.keyword("host-call")
    HOST_FIELD = kw.keyword("host-field")
    HOST_INTEROP = kw.keyword("host-interop")
    IF = kw.keyword("if")
    IMPORT = kw.keyword("import")
    IMPORT_ALIAS = kw.keyword("import-alias")
    INVOKE = kw.keyword("invoke")
    LET = kw.keyword("let")
    LETFN = kw.keyword("letfn")
    LOCAL = kw.keyword("local")
    LOOP = kw.keyword("loop")
    MAP = kw.keyword("map")
    MAYBE_CLASS = kw.keyword("maybe-class")
    MAYBE_HOST_FORM = kw.keyword("maybe-host-form")
    PY_DICT = kw.keyword("py-dict")
    PY_LIST = kw.keyword("py-list")
    PY_SET = kw.keyword("py-set")
    PY_TUPLE = kw.keyword("py-tuple")
    QUEUE = kw.keyword("queue")
    QUOTE = kw.keyword("quote")
    RECUR = kw.keyword("recur")
    REIFY = kw.keyword("reify")
    REQUIRE = kw.keyword("require")
    REQUIRE_ALIAS = kw.keyword("require-alias")
    SET = kw.keyword("set")
    SET_BANG = kw.keyword("set!")
    THROW = kw.keyword("throw")
    TRY = kw.keyword("try")
    VAR = kw.keyword("var")
    VECTOR = kw.keyword("vector")
    WITH_META = kw.keyword("with-meta")
    YIELD = kw.keyword("yield")


T = TypeVar("T")


class Node(ABC, Generic[T]):
    __slots__ = ()

    @property
    @abstractmethod
    def op(self) -> NodeOp:
        """Enumerated keyword uniquely identifying this type of Node.

        The type and NodeOp should always be in sync.

        Having a simple enum value in addition to the type allows us to use
        switch-like syntax with Python dictionaries in compiler code, which
        is much faster than performing isinstance checks."""

    @property
    @abstractmethod
    def form(self) -> T:
        """The original Lisp form corresponding to this Node."""

    @property
    @abstractmethod
    def children(self) -> Iterable[kw.Keyword]:
        """An iterable of keywords naming the attributes on the node which
        contain child nodes used for visiting all nodes in a tree.

        In most cases, children are safely defaulted at class definition.
        For certain nodes, the children may be variable and must be set at
        construction.

        Initially, this property was typed as a typing.Collection. In practice,
        children is always a Vector. However, MyPy seems to have trouble
        identifying IPersistentVector-typed attributes as typing.Collection, so
        this type was set as typing.Iterable which seems to work for now."""

    @property
    @abstractmethod
    def raw_forms(self) -> IPersistentVector[LispForm]:
        """A collection of intermediate forms produced by macros before emitting
        the final form.

        As with children above, this was formerly a more generic typing.Collection
        type, but MyPy was returning an error. If this error is ever fixed,
        this should be returned to a more protocol type."""

    @property
    @abstractmethod
    def top_level(self) -> bool:
        """True if this node is the root of the entire syntax tree, False
        otherwise.

        In practice, things such as def forms will end up being top level nodes,
        though at the REPL even something simple like a Const node could be top
        level."""

    @property
    @abstractmethod
    def env(self) -> "NodeEnv":
        """Details about the environment of the original form such as line and
        column numbers."""

    def to_map(self) -> lmap.PersistentMap:
        return to_lisp(attr.asdict(self))  # type: ignore[arg-type]

    def assoc(self, **kwargs):
        return attr.evolve(self, **kwargs)  # type: ignore[misc]

    def visit(self, f: Callable[..., None], *args, **kwargs):
        """Visit all immediate children of this node, calling
        f(child, *args, **kwargs) on each child."""
        for child_kw in self.children:
            child_attr = munge(child_kw.name)

            if child_attr.endswith("s"):
                iter_child: Iterable[Node] = getattr(self, child_attr)
                assert iter_child is not None, "Listed child must not be none"
                for item in iter_child:
                    f(item, *args, **kwargs)
            else:
                child: Node = getattr(self, child_attr)
                assert child is not None, "Listed child must not be none"
                f(child, *args, **kwargs)

    def fix_missing_locations(
        self, form_loc: Optional[tuple[int, int, int, int]] = None
    ) -> "Node[T]":
        """Return a transformed copy of this node with location in this node's
        environment updated to match the `form_loc` if given, or using its
        existing location otherwise. All child nodes will be recursively
        transformed and replaced. Child nodes will use their parent node
        location if they do not have one."""
        if (
            self.env.line is None
            or self.env.col is None
            or self.env.end_line is None
            or self.env.end_col is None
        ):
            loc = form_loc
        else:
            loc = (self.env.line, self.env.col, self.env.end_line, self.env.end_col)

        assert loc is not None and all(
            e is not None for e in loc
        ), "Must specify location information"

        new_attrs: MutableMapping[str, Union[NodeEnv, Node, Iterable[Node]]] = {
            "env": attr.evolve(
                self.env, line=loc[0], col=loc[1], end_line=loc[2], end_col=loc[3]
            )
        }
        for child_kw in self.children:
            child_attr = munge(child_kw.name)
            assert child_attr != "env", "Node environment already set"

            if child_attr.endswith("s"):
                iter_child: Iterable[Node] = getattr(self, child_attr)
                assert iter_child is not None, "Listed child must not be none"
                new_attrs[child_attr] = vec.vector(
                    item.fix_missing_locations(form_loc) for item in iter_child
                )
            else:
                child: Node = getattr(self, child_attr)
                assert child is not None, "Listed child must not be none"
                new_attrs[child_attr] = child.fix_missing_locations(form_loc)

        return self.assoc(**new_attrs)


def deftype_or_reify_python_member_names(
    members: Iterable["DefTypeMember"],
) -> Iterable[str]:
    """Yield successive munged Python names for `deftype*` and `reify*` members.

    For multi-arity methods, both the outer dispatch method and each inner arity
    will be yielded."""
    for member in members:
        yield member.python_name
        if isinstance(member, DefTypeMethod):
            if len(member.arities) > 1:
                for arity in member.arities:
                    yield arity.python_name


class Assignable(ABC):
    __slots__ = ()

    @property
    @abstractmethod
    def is_assignable(self) -> bool:
        """True if this Node can be assigned in a set! form, False otherwise."""


class NodeSyntacticPosition(Enum):
    STMT = kw.keyword("stmt")
    EXPR = kw.keyword("expr")


class ConstType(Enum):
    NIL = kw.Keyword("nil")
    MAP = kw.keyword("map")
    QUEUE = kw.keyword("queue")
    SET = kw.keyword("set")
    VECTOR = kw.keyword("vector")
    BOOL = kw.keyword("bool")
    BYTES = kw.keyword("bytes")
    KEYWORD = kw.keyword("keyword")
    SYMBOL = kw.keyword("symbol")
    STRING = kw.keyword("string")
    NUMBER = kw.keyword("number")
    DECIMAL = kw.keyword("decimal")
    FRACTION = kw.keyword("fraction")
    RECORD = kw.keyword("record")
    TYPE = kw.keyword("type")
    SEQ = kw.keyword("seq")
    CHAR = kw.keyword("char")
    REGEX = kw.keyword("regex")
    CLASS = kw.keyword("class")
    INST = kw.keyword("inst")
    UUID = kw.keyword("uuid")
    PY_DICT = kw.keyword("py-dict")
    PY_LIST = kw.keyword("py-list")
    PY_SET = kw.keyword("py-set")
    PY_TUPLE = kw.keyword("py-tuple")
    UNKNOWN = kw.keyword("unknown")


KeywordArgs = IPersistentMap[str, Node]
NodeMeta = Union[None, "Const", "Map"]
LoopID = str


class FunctionContextType(Enum):
    FUNCTION = kw.keyword("function")
    ASYNC_FUNCTION = kw.keyword("async-function")
    METHOD = kw.keyword("method")
    CLASSMETHOD = kw.keyword("classmethod")
    STATICMETHOD = kw.keyword("staticmethod")
    PROPERTY = kw.keyword("property")


@attr.define
class FunctionContext:
    function_type: FunctionContextType
    is_generator: bool = False


class KeywordArgSupport(Enum):
    APPLY_KWARGS = kw.keyword("apply")
    COLLECT_KWARGS = kw.keyword("collect")


class LocalType(Enum):
    ARG = kw.keyword("arg")
    CATCH = kw.keyword("catch")
    DEFTYPE = kw.keyword("deftype")
    FIELD = kw.keyword("field")
    FN = kw.keyword("fn")
    IMPORT = kw.keyword("import")
    LET = kw.keyword("let")
    LETFN = kw.keyword("letfn")
    LOOP = kw.keyword("loop")
    THIS = kw.keyword("this")


@attr.frozen
class NodeEnv:
    ns: Namespace
    file: str
    line: Optional[int] = None
    col: Optional[int] = None
    end_line: Optional[int] = None
    end_col: Optional[int] = None
    pos: Optional[NodeSyntacticPosition] = None
    func_ctx: Optional[FunctionContext] = None


@attr.frozen
class Await(Node[ReaderLispForm]):
    form: ReaderLispForm
    expr: Node
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.v(EXPR)
    op: NodeOp = NodeOp.AWAIT
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class Binding(Node[sym.Symbol], Assignable):
    form: sym.Symbol
    name: str
    local: LocalType
    env: NodeEnv = attr.field(hash=False)
    tag: Optional[Node] = None
    arg_id: Optional[int] = None
    is_variadic: bool = False
    is_assignable: bool = False
    init: Optional[Node] = None
    meta: NodeMeta = None
    children: Sequence[kw.Keyword] = vec.EMPTY
    op: NodeOp = NodeOp.BINDING
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class Catch(Node[SpecialForm]):
    form: SpecialForm
    class_: Union["MaybeClass", "MaybeHostForm"]
    local: Binding
    body: "Do"
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.v(CLASS, LOCAL, BODY)
    op: NodeOp = NodeOp.CATCH
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class Const(Node[ReaderLispForm]):
    form: ReaderLispForm
    type: ConstType
    val: ReaderLispForm
    is_literal: bool
    env: NodeEnv = attr.field(hash=False)
    meta: NodeMeta = None
    children: Sequence[kw.Keyword] = vec.EMPTY
    op: NodeOp = NodeOp.CONST
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class Def(Node[SpecialForm]):
    form: SpecialForm
    name: sym.Symbol
    var: Var
    init: Optional[Node]
    doc: Optional[str]
    env: NodeEnv = attr.field(hash=False)
    tag: Optional[Node] = None
    meta: NodeMeta = None
    children: Sequence[kw.Keyword] = vec.EMPTY
    op: NodeOp = NodeOp.DEF
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


DefTypeBase = Union["MaybeClass", "MaybeHostForm", "VarRef"]


@attr.frozen
class DefType(Node[SpecialForm]):
    form: SpecialForm
    name: str
    interfaces: Iterable[DefTypeBase]
    fields: Iterable[Binding]
    members: Iterable["DefTypeMember"]
    env: NodeEnv = attr.field(hash=False)
    verified_abstract: bool = False
    artificially_abstract: IPersistentSet[DefTypeBase] = lset.EMPTY
    is_frozen: bool = True
    use_slots: bool = True
    use_weakref_slot: bool = True
    meta: NodeMeta = None
    children: Sequence[kw.Keyword] = vec.v(FIELDS, MEMBERS)
    op: NodeOp = NodeOp.DEFTYPE
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY

    @property
    def python_member_names(self) -> Iterable[str]:
        yield from deftype_or_reify_python_member_names(self.members)


@attr.frozen
class DefTypeMember(Node[SpecialForm]):
    form: SpecialForm
    name: str
    env: NodeEnv = attr.field(hash=False)

    @property
    def python_name(self) -> str:
        return munge(self.name)


@attr.frozen
class DefTypeClassMethod(DefTypeMember):
    class_local: Binding
    params: Iterable[Binding]
    fixed_arity: int
    body: "Do"
    is_variadic: bool = False
    kwarg_support: Optional[KeywordArgSupport] = None
    children: Sequence[kw.Keyword] = vec.v(CLASS_LOCAL, PARAMS, BODY)
    op: NodeOp = NodeOp.DEFTYPE_CLASSMETHOD
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class DefTypeMethod(DefTypeMember):
    max_fixed_arity: int
    arities: IPersistentVector["DefTypeMethodArity"]
    is_variadic: bool = False
    children: Sequence[kw.Keyword] = vec.v(ARITIES)
    op: NodeOp = NodeOp.DEFTYPE_METHOD
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class DefTypeMethodArity(Node[SpecialForm]):
    form: SpecialForm
    name: str
    params: Iterable[Binding]
    fixed_arity: int
    body: "Do"
    this_local: Binding
    loop_id: LoopID
    env: NodeEnv = attr.field(hash=False)
    is_variadic: bool = False
    kwarg_support: Optional[KeywordArgSupport] = None
    children: Sequence[kw.Keyword] = vec.v(THIS_LOCAL, PARAMS, BODY)
    op: NodeOp = NodeOp.DEFTYPE_METHOD_ARITY
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY

    @property
    def python_name(self) -> str:
        return f"_{munge(self.name)}_arity{'_rest' if self.is_variadic else self.fixed_arity}"


@attr.frozen
class DefTypeProperty(DefTypeMember):
    this_local: Binding
    params: Iterable[Binding]
    body: "Do"
    children: Sequence[kw.Keyword] = vec.v(THIS_LOCAL, PARAMS, BODY)
    op: NodeOp = NodeOp.DEFTYPE_PROPERTY
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class DefTypeStaticMethod(DefTypeMember):
    params: Iterable[Binding]
    fixed_arity: int
    body: "Do"
    is_variadic: bool = False
    kwarg_support: Optional[KeywordArgSupport] = None
    children: Sequence[kw.Keyword] = vec.v(PARAMS, BODY)
    op: NodeOp = NodeOp.DEFTYPE_STATICMETHOD
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


DefTypePythonMember = Union[DefTypeClassMethod, DefTypeProperty, DefTypeStaticMethod]


@attr.frozen
class Do(Node[SpecialForm]):
    form: SpecialForm
    statements: Iterable[Node]
    ret: Node
    env: NodeEnv = attr.field(hash=False)
    is_body: bool = False
    use_var_indirection: bool = False
    children: Sequence[kw.Keyword] = vec.v(STATEMENTS, RET)
    op: NodeOp = NodeOp.DO
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class Fn(Node[SpecialForm]):
    form: SpecialForm
    max_fixed_arity: int
    arities: IPersistentVector["FnArity"]
    env: NodeEnv = attr.field(hash=False)
    local: Optional[Binding] = None
    is_variadic: bool = False
    is_async: bool = False
    kwarg_support: Optional[KeywordArgSupport] = None
    inline_fn: Optional["Fn"] = None
    children: Sequence[kw.Keyword] = vec.v(ARITIES)
    op: NodeOp = NodeOp.FN
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class FnArity(Node[SpecialForm]):
    form: SpecialForm
    loop_id: LoopID
    params: Iterable[Binding]
    fixed_arity: int
    body: Do
    env: NodeEnv = attr.field(hash=False)
    tag: Optional[Node] = None
    is_variadic: bool = False
    children: Sequence[kw.Keyword] = vec.v(PARAMS, BODY)
    op: NodeOp = NodeOp.FN_ARITY
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class HostCall(Node[SpecialForm]):
    form: SpecialForm
    method: str
    target: Node
    args: Iterable[Node]
    kwargs: KeywordArgs
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.v(TARGET, ARGS)
    op: NodeOp = NodeOp.HOST_CALL
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class HostField(Node[Union[SpecialForm, sym.Symbol]], Assignable):
    form: Union[SpecialForm, sym.Symbol]
    field: str
    target: Node
    env: NodeEnv = attr.field(hash=False)
    is_assignable: bool = True
    children: Sequence[kw.Keyword] = vec.v(TARGET)
    op: NodeOp = NodeOp.HOST_FIELD
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class If(Node[SpecialForm]):
    form: SpecialForm
    test: Node
    then: Node
    env: NodeEnv = attr.field(hash=False)
    else_: Node
    children: Sequence[kw.Keyword] = vec.v(TEST, THEN, ELSE)
    op: NodeOp = NodeOp.IF
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class Import(Node[SpecialForm]):
    form: SpecialForm
    aliases: Iterable["ImportAlias"]
    refers: Iterable[str]
    refer_all: bool
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.EMPTY
    op: NodeOp = NodeOp.IMPORT
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class ImportAlias(Node[Union[sym.Symbol, vec.PersistentVector]]):
    form: Union[sym.Symbol, vec.PersistentVector]
    name: str
    alias: Optional[str]
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.EMPTY
    op: NodeOp = NodeOp.IMPORT_ALIAS
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class Invoke(Node[SpecialForm]):
    form: SpecialForm
    fn: Node
    args: Iterable[Node]
    kwargs: KeywordArgs
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.v(FN, ARGS)
    op: NodeOp = NodeOp.INVOKE
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class Let(Node[SpecialForm]):
    form: SpecialForm
    bindings: Iterable[Binding]
    body: Do
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.v(BINDINGS, BODY)
    op: NodeOp = NodeOp.LET
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class LetFn(Node[SpecialForm]):
    form: SpecialForm
    bindings: Iterable[Binding]
    body: Do
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.v(BINDINGS, BODY)
    op: NodeOp = NodeOp.LETFN
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class Local(Node[sym.Symbol], Assignable):
    form: sym.Symbol
    name: str
    local: LocalType
    env: NodeEnv = attr.field(hash=False)
    is_assignable: bool = False
    arg_id: Optional[int] = None
    is_variadic: bool = False
    children: Sequence[kw.Keyword] = vec.EMPTY
    op: NodeOp = NodeOp.LOCAL
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class Loop(Node[SpecialForm]):
    form: SpecialForm
    bindings: Iterable[Binding]
    body: Do
    loop_id: LoopID
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.v(BINDINGS, BODY)
    op: NodeOp = NodeOp.LOOP
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class Map(Node[IPersistentMap]):
    form: IPersistentMap
    keys: Iterable[Node]
    vals: Iterable[Node]
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.v(KEYS, VALS)
    op: NodeOp = NodeOp.MAP
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class MaybeClass(Node[sym.Symbol]):
    form: sym.Symbol
    class_: str
    target: Any
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.EMPTY
    op: NodeOp = NodeOp.MAYBE_CLASS
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class MaybeHostForm(Node[sym.Symbol]):
    form: sym.Symbol
    class_: str
    field: str
    target: Any
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.EMPTY
    op: NodeOp = NodeOp.MAYBE_HOST_FORM
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen(eq=True)
class PyDict(Node[dict]):
    form: dict
    keys: Iterable[Node]
    vals: Iterable[Node]
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.v(KEYS, VALS)
    op: NodeOp = NodeOp.PY_DICT
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen(eq=True)
class PyList(Node[list]):
    form: list
    items: Iterable[Node]
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.v(ITEMS)
    op: NodeOp = NodeOp.PY_LIST
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen(eq=True)
class PySet(Node[Union[frozenset, set]]):
    form: Union[frozenset, set]
    items: Iterable[Node]
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.v(ITEMS)
    op: NodeOp = NodeOp.PY_SET
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class PyTuple(Node[tuple]):
    form: tuple
    items: Iterable[Node]
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.v(ITEMS)
    op: NodeOp = NodeOp.PY_TUPLE
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class Queue(Node[lqueue.PersistentQueue]):
    form: lqueue.PersistentQueue
    items: Iterable[Node]
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.v(ITEMS)
    op: NodeOp = NodeOp.QUEUE
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class Quote(Node[SpecialForm]):
    form: SpecialForm
    expr: Const
    env: NodeEnv = attr.field(hash=False)
    is_literal: bool = True
    children: Sequence[kw.Keyword] = vec.v(EXPR)
    op: NodeOp = NodeOp.QUOTE
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class Recur(Node[SpecialForm]):
    form: SpecialForm
    exprs: Iterable[Node]
    loop_id: LoopID
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.v(EXPRS)
    op: NodeOp = NodeOp.RECUR
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class Reify(Node[SpecialForm]):
    form: SpecialForm
    interfaces: Iterable[DefTypeBase]
    members: Iterable["DefTypeMember"]
    env: NodeEnv = attr.field(hash=False)
    verified_abstract: bool = False
    artificially_abstract: IPersistentSet[DefTypeBase] = lset.EMPTY
    is_frozen: bool = True
    use_weakref_slot: bool = True
    meta: NodeMeta = None
    children: Sequence[kw.Keyword] = vec.v(MEMBERS)
    op: NodeOp = NodeOp.REIFY
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY

    @property
    def python_member_names(self) -> Iterable[str]:
        yield from deftype_or_reify_python_member_names(self.members)


@attr.frozen
class RequireAlias(Node[Union[sym.Symbol, vec.PersistentVector]]):
    form: Union[sym.Symbol, vec.PersistentVector]
    name: str
    alias: Optional[str]
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.EMPTY
    op: NodeOp = NodeOp.REQUIRE_ALIAS
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class Require(Node[SpecialForm]):
    form: SpecialForm
    aliases: Iterable[RequireAlias]
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.EMPTY
    op: NodeOp = NodeOp.REQUIRE
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class Set(Node[IPersistentSet]):
    form: IPersistentSet
    items: Iterable[Node]
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.v(ITEMS)
    op: NodeOp = NodeOp.SET
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class SetBang(Node[SpecialForm]):
    form: SpecialForm
    target: Union[Assignable, Node]
    val: Node
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.v(TARGET, VAL)
    op: NodeOp = NodeOp.SET_BANG
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class Throw(Node[SpecialForm]):
    form: SpecialForm
    exception: Node
    cause: Optional[Node]
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.v(EXCEPTION)
    op: NodeOp = NodeOp.THROW
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class Try(Node[SpecialForm]):
    form: SpecialForm
    body: Do
    catches: Iterable[Catch]
    children: Sequence[kw.Keyword]
    env: NodeEnv = attr.field(hash=False)
    finally_: Optional[Do] = None
    op: NodeOp = NodeOp.TRY
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class VarRef(Node[Union[sym.Symbol, ISeq]], Assignable):
    form: Union[sym.Symbol, ISeq]
    var: Var
    env: NodeEnv = attr.field(hash=False)
    return_var: bool = False
    is_assignable: bool = True
    is_allow_var_indirection: bool = False
    children: Sequence[kw.Keyword] = vec.EMPTY
    op: NodeOp = NodeOp.VAR
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class Vector(Node[IPersistentVector]):
    form: IPersistentVector
    items: Iterable[Node]
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.v(ITEMS)
    op: NodeOp = NodeOp.VECTOR
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


T_withmeta = TypeVar("T_withmeta", Fn, Map, Queue, Reify, Set, Vector)


@attr.frozen
class WithMeta(Node[LispForm], Generic[T_withmeta]):
    form: LispForm
    meta: Union[Const, Map]
    expr: T_withmeta
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.v(META, EXPR)
    op: NodeOp = NodeOp.WITH_META
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY


@attr.frozen
class Yield(Node[SpecialForm]):
    form: SpecialForm
    expr: Optional[Node]
    env: NodeEnv = attr.field(hash=False)
    children: Sequence[kw.Keyword] = vec.v(EXPR)
    op: NodeOp = NodeOp.YIELD
    top_level: bool = False
    raw_forms: IPersistentVector[LispForm] = vec.EMPTY

    @classmethod
    def expressionless(cls, form: SpecialForm, env: NodeEnv):
        return cls(form=form, expr=None, env=env, children=vec.EMPTY)


SpecialFormNode = Union[
    Await,
    Def,
    DefType,
    Do,
    Fn,
    If,
    HostCall,
    HostField,
    Import,
    Invoke,
    Let,
    LetFn,
    Loop,
    Quote,
    Recur,
    Reify,
    Require,
    SetBang,
    Throw,
    Try,
    VarRef,
    Yield,
]
