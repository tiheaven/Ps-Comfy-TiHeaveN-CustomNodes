import os
import json
import logging
from aiohttp import web
import folder_paths
from server import PromptServer  # 导入 ComfyUI 服务器实例
import comfy
from comfy_execution.graph_utils import GraphBuilder, Node  # 导入graph_utils中的工具类

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Ps-Comfy-TiHeaveN-WorkflowRouteHook")

# 获取 ComfyUI 默认工作流目录（user/default/workflows）
def get_default_workflow_dir():
    """动态获取 ComfyUI 标准工作流目录，避免硬编码"""
    # 优先通过 folder_paths 获取 user 目录
    user_root = folder_paths.get_directory_by_type("user")
    if not user_root:
        # 兼容旧版本或自定义配置
        user_root = os.path.join(folder_paths.base_path, "user")
    
    # 构建标准工作流路径：user/default/workflows
    workflow_dir = os.path.join(user_root, "default", "workflows")
    # 确保目录存在（首次使用时自动创建）
    os.makedirs(workflow_dir, exist_ok=True)
    return workflow_dir

# 初始化工作流目录
WORKFLOW_DIR = get_default_workflow_dir()
#logger.info(f"已挂钩工作流目录: {WORKFLOW_DIR}")


def convert_workflow_format(original_workflow):
    """将原始工作流格式转换为目标格式，忽略mode=4和Reroute节点并保持连接正确，同时按规则处理widget值和title"""
    builder = GraphBuilder(prefix="")  # 使用空前缀以便保持原始ID
    nodes = original_workflow.get("nodes", [])
    links = original_workflow.get("links", [])  # links格式: [link_id, 源节点id, 源输出索引, 目标节点id, 目标输入索引, 类型]

    # 第一步：收集所有被忽略的节点ID（mode=4或类型为Reroute）和正常节点ID
    ignore_node_ids = set()
    normal_node_ids = set()
    for node in nodes:
        node_id = str(node["id"])
        # 忽略mode=4的节点和Reroute节点
        if node.get("mode") == 4 or node.get("type") == "Reroute":
            ignore_node_ids.add(node_id)
        else:
            normal_node_ids.add(node_id)

    # 辅助函数：根据输出类型匹配被忽略节点的对应输入
    def get_corresponding_input(ignored_node, output_type):
        for input_data in ignored_node.get("inputs", []):
            if input_data.get("link") is not None:
                return input_data
        return None

    # 辅助函数：递归找到上游第一个正常节点（穿透被忽略节点）
    def find_original_source(link_id):
        for link in links:
            if link[0] == link_id:  # link[0]是link_id
                source_node_id = str(link[1])  # 源节点ID
                source_out_idx = link[2]       # 源节点输出索引
                output_type = link[5]          # 链接类型（即源节点输出类型）

                if source_node_id in normal_node_ids:
                    return [source_node_id, source_out_idx]
                elif source_node_id in ignore_node_ids:
                    source_node = next((n for n in nodes if str(n["id"]) == source_node_id), None)
                    if not source_node:
                        return None
                    corresponding_input = get_corresponding_input(source_node, output_type)
                    if corresponding_input and corresponding_input.get("link") is not None:
                        return find_original_source(corresponding_input["link"])
        return None

    # 新增辅助函数：检查值是否为字典且包含列表类型的值
    def has_list_in_dict(value):
        # 若值是字典，且字典中至少有一个值是列表，则返回True
        return isinstance(value, dict) and any(isinstance(v, list) for v in value.values())

    # 第二步：只添加正常节点（mode≠4），并处理输入链接、title、localized_names和types
    for node in nodes:
        node_id = str(node["id"])
        if node_id in ignore_node_ids:
            continue  # 跳过被忽略节点

        class_type = node["type"]
        # 处理title
        node_title = node.get("title")
        if node_title is None:
            properties = node.get("properties", {})
            node_title = properties.get("Node name for S&R", "")
            if not node_title:
                node_title = class_type
        
        inputs = {}
        localized_names = []
        types = []
        
        # 收集带widget的输入名称（按输入顺序）
        widget_input_names = [
            inp["name"] for inp in node.get("inputs", [])
            if inp.get("widget") is not None
        ]
        
        # 过滤widgets_values中的"randomize"
        strings_to_filter = {"randomize", "fixed", "increment", "decrement"}
        filtered_widget_values = [
            v for v in node.get("widgets_values", []) 
            #if v not in strings_to_filter
            if not isinstance(v, list) and v not in strings_to_filter  # 新增列表类型判断
        ]
        
        for input_data in node.get("inputs", []):
            input_name = input_data["name"]
            
            # -------------------------- 修正后的过滤逻辑 --------------------------
            # 当字段值为字典，且字典中包含列表时，忽略该字段（不限制字段名）
            # 1. 先获取当前字段的值（可能来自link或widget）
            current_value = None
            if input_data.get("link") is not None:
                # 从链接获取值（通常是上游节点引用，格式为[节点ID, 输出索引]）
                original_source = find_original_source(input_data["link"])
                current_value = original_source
            else:
                # 从widget获取值
                if input_data.get("widget") is not None:
                    try:
                        widget_index = widget_input_names.index(input_name)
                        if widget_index < len(filtered_widget_values):
                            current_value = filtered_widget_values[widget_index]
                    except ValueError:
                        pass  # 不在widget列表中，不处理
            
            # 2. 检查是否符合过滤条件：值是字典，且字典中包含列表
            if has_list_in_dict(current_value):
                logger.info(f"节点 {node_id} 的字段 {input_name} 值为含列表的字典，已跳过")
                continue  # 跳过当前字段的所有处理
            # -----------------------------------------------------------------

            # 处理localized_names
            label = input_data.get("label")
            localized_name = input_data.get("localized_name")
            display_value = label if label is not None else localized_name
            if display_value is not None:
                localized_names.append({input_name: display_value})
            
            # 处理types
            if input_data.get("widget") is not None:
                input_type = input_data.get("type")
                if input_type is not None:
                    types.append({input_name: input_type})
            
            # 处理输入链接或widget值（已通过过滤逻辑的字段才会执行到这里）
            if input_data.get("link") is not None:
                original_source = find_original_source(input_data["link"])
                if original_source:
                    inputs[input_name] = tuple(original_source)
                else:
                    logger.warning(f"节点 {node_id} 的输入 {input_name} 无法找到有效源，已忽略")
            else:
                if input_data.get("widget") is not None:
                    try:
                        widget_index = widget_input_names.index(input_name)
                        if widget_index < len(filtered_widget_values):
                            inputs[input_name] = filtered_widget_values[widget_index]
                        else:
                            logger.warning(f"节点 {node_id} 的输入 {input_name} 没有对应的widget值（过滤后）")
                    except ValueError as e:
                        logger.warning(f"节点 {node_id} 的输入 {input_name} 不在widget输入列表中: {e}")
        
        # 构建_meta信息
        _meta = {"title": node_title} if node_title else {}
        
        # 暂存localized_names和types
        inputs["_localized_names"] = localized_names
        inputs["_types"] = types
        
        # 添加节点到构建器
        builder.node(class_type, id=node_id, _meta=_meta, **inputs)

    # 第三步：调整_meta等字段位置
    finalized = builder.finalize()
    for node_id, node_data in finalized.items():
        if "_meta" in node_data["inputs"]:
            node_data["_meta"] = node_data["inputs"].pop("_meta")
        if "_localized_names" in node_data["inputs"]:
            node_data["localized_names"] = node_data["inputs"].pop("_localized_names")
        if "_types" in node_data["inputs"]:
            node_data["types"] = node_data["inputs"].pop("_types")
    
    return finalized


async def handle_get_workflow(request):
    """处理获取单个工作流文件的请求（/workflows/{filename}）"""
    filename = request.match_info.get("filename")
    if not filename or not filename.endswith(".json"):
        return web.Response(status=400, text="无效的文件名（必须是 .json 文件）")

    # 构建文件路径并验证安全性（防止目录遍历攻击）
    filepath = os.path.abspath(os.path.join(WORKFLOW_DIR, filename))
    if os.path.commonpath((WORKFLOW_DIR, filepath)) != WORKFLOW_DIR:
        return web.Response(status=403, text="访问被拒绝：无效路径")

    # 读取并返回工作流内容
    if not os.path.isfile(filepath):
        return web.Response(status=404, text="工作流文件不存在")

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            original_workflow = json.load(f)
        
        # 转换工作流格式
        converted_workflow = convert_workflow_format(original_workflow)
        
        return web.json_response(converted_workflow)
    except json.JSONDecodeError:
        return web.Response(status=500, text="工作流文件 JSON 格式无效")
    except Exception as e:
        logger.error(f"读取工作流失败: {str(e)}")
        return web.Response(status=500, text="读取工作流文件时发生错误")


async def list_workflows(request):
    """处理目录列表请求（/workflows/ 或 /workflows/subdir/）"""
    subpath = request.match_info.get("subpath", "")
    # 构建目标目录路径
    target_dir = os.path.abspath(os.path.join(WORKFLOW_DIR, subpath))

    # 安全验证：确保访问路径在允许的工作流目录内
    if os.path.commonpath((WORKFLOW_DIR, target_dir)) != WORKFLOW_DIR:
        return web.Response(status=403, text="访问被拒绝：无效路径")

    # 检查目录是否存在
    if not os.path.isdir(target_dir):
        return web.Response(status=404, text="目录不存在")

    files = []
    directories = []

    try:
        # 遍历目录内容
        for entry in os.scandir(target_dir):
            if entry.is_file():
                # 仅保留 JSON 文件
                if entry.name.endswith(".json"):
                    stat = entry.stat()
                    files.append({
                        "name": entry.name,
                        "size": stat.st_size,  # 文件大小（字节）
                        "modified": stat.st_mtime  # 最后修改时间（时间戳）
                    })
            elif entry.is_dir():
                # 收集子目录信息
                rel_path = os.path.relpath(entry.path, WORKFLOW_DIR).replace(os.sep, "/")
                directories.append({
                    "name": entry.name,
                    "path": rel_path  # 相对路径（用于客户端构建子目录 URL）
                })

        # 返回结构化目录信息
        return web.json_response({
            "directory": os.path.relpath(target_dir, WORKFLOW_DIR).replace(os.sep, "/"),
            "files": files,
            "directories": directories
        })
    except Exception as e:
        logger.error(f"列出工作流失败: {str(e)}")
        return web.Response(status=500, text="列出工作流时发生错误")


def get_locales_dir():
    """获取 locales 目录路径"""
    # 基于当前文件路径定位 locales 目录
    current_dir = os.path.dirname(os.path.abspath(__file__))
    locales_dir = os.path.join(current_dir, "locales")
    os.makedirs(locales_dir, exist_ok=True)  # 确保目录存在
    return locales_dir


async def list_locales(request):
    """处理获取 locales 目录下文件列表的请求"""
    locales_dir = get_locales_dir()
    
    try:
        files = []
        # 遍历目录下所有 .json 文件
        for entry in os.scandir(locales_dir):
            if entry.is_file() and entry.name.endswith(".json"):
                stat = entry.stat()
                files.append({
                    "name": entry.name,
                    "size": stat.st_size,
                    "modified": stat.st_mtime
                })
        
        return web.json_response({
            "directory": "locales",
            "files": files
        })
    except Exception as e:
        logger.error(f"列出语言文件失败: {str(e)}")
        return web.Response(status=500, text="列出语言文件时发生错误")


async def handle_get_locale(request):
    """处理获取指定语言包内容的请求（仅返回原始JSON内容，由客户端处理）"""
    filename = request.match_info.get("filename")
    if not filename or not filename.endswith(".json"):
        return web.Response(status=400, text="无效的文件名（必须是 .json 文件）")

    locales_dir = get_locales_dir()
    # 构建文件路径并验证安全性（防止路径遍历攻击）
    filepath = os.path.abspath(os.path.join(locales_dir, filename))
    if os.path.commonpath((locales_dir, filepath)) != locales_dir:
        return web.Response(status=403, text="访问被拒绝：无效路径")

    if not os.path.isfile(filepath):
        return web.Response(status=404, text="语言文件不存在")

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()  # 直接读取原始JSON内容
        
        # 返回JSON内容，设置标准JSON的Content-Type
        return web.Response(
            body=content,
            content_type="application/json",  # JSON标准MIME类型
            charset="utf-8",  # 明确指定UTF-8编码（JSON默认应使用UTF-8）
            status=200
        )
    except Exception as e:
        logger.error(f"读取语言文件失败: {str(e)}")
        return web.Response(status=500, text="读取语言文件时发生错误")
    
def get_app_version():
    """
    无依赖从pyproject.toml读取version字段
    仅解析[project]块下的version = "x.x.x"格式，支持单/双引号、行内空格、行尾注释
    """
    # 获取pyproject.toml的路径（与当前节点文件同级）
    current_dir = os.path.dirname(os.path.abspath(__file__))
    toml_path = os.path.join(current_dir, "pyproject.toml")

    # 检查文件是否存在
    if not os.path.exists(toml_path):
        logger.error(f"未找到pyproject.toml文件，路径：{toml_path}")
        return "0.0.0"

    # 标记是否进入[project]块
    in_project_block = False
    version = "0.0.0"

    try:
        with open(toml_path, "r", encoding="utf-8") as f:
            for line_num, line in enumerate(f, 1):
                # 去除行首行尾空白（空格、制表符、换行）
                stripped_line = line.strip()

                # 跳过空行和注释行（TOML注释以#开头）
                if not stripped_line or stripped_line.startswith("#"):
                    continue

                # 检测[project]块的开始
                if stripped_line.startswith("[project]"):
                    in_project_block = True
                    continue

                # 检测其他块的开始（退出[project]块）
                if stripped_line.startswith("[") and in_project_block:
                    break

                # 仅在[project]块内解析version字段
                if in_project_block and stripped_line.lower().startswith("version"):
                    # 分割键值对：处理"version = x.x.x" "version= x.x.x"等格式
                    key_value = stripped_line.split("=", 1)
                    if len(key_value) != 2:
                        continue

                    # 提取值部分并清理（去除空格、引号、注释）
                    value_part = key_value[1].strip()
                    # 去除行尾注释（如 version = "1.0.0" # 版本号）
                    if "#" in value_part:
                        value_part = value_part.split("#", 1)[0].strip()
                    # 去除单/双引号
                    value_part = value_part.strip("'\"")

                    # 验证版本号非空（简单校验）
                    if value_part and "." in value_part:
                        version = value_part
                        #logger.info(f"从pyproject.toml读取版本号成功：{version}")
                    break

    except Exception as e:
        logger.error(f"解析pyproject.toml失败：{str(e)}")
        version = "0.0.0"

    return version

async def handle_get_appinfo(request):
    """处理/ps-comfy-tiheaven-appinfo路由请求，返回版本号JSON"""
    version = get_app_version()
    return web.json_response({"version": version})

def register_appinfo_route():
    """注册路由到ComfyUI的PromptServer"""
    server = PromptServer.instance
    if not server:
        logger.error("注册路由失败：未找到 PromptServer 实例")
        return

    # 定义并注册路由
    appinfo_routes = web.RouteTableDef()
    appinfo_routes.get("/ps-comfy-tiheaven-appinfo")(handle_get_appinfo)
    PromptServer.instance.app.router.add_routes(appinfo_routes)
    logger.info("成功注册无依赖版路由：/ps-comfy-tiheaven-appinfo")
        
def register_workflow_routes():
    """向 ComfyUI 服务器注册工作流相关路由"""
    server = PromptServer.instance
    if not server:
        logger.error("注册路由失败：未找到 PromptServer 实例")
        return

    # 定义自定义路由表
    custom_routes = web.RouteTableDef()
    
    # 仅匹配以 .json 结尾的文件名（使用正则约束）
    custom_routes.get(r"/workflows/{filename:.*\.json}")(handle_get_workflow)
    # 目录路由保持不变（匹配所有其他路径）
    custom_routes.get("/workflows/{subpath:.*}")(list_workflows)

    # 将路由添加到服务器
    server.app.router.add_routes(custom_routes)
    #logger.info("工作流路由已成功挂钩：支持目录列表和文件获取")

def register_locale_routes():
    """注册语言文件相关路由"""
    server = PromptServer.instance
    if not server:
        logger.error("注册语言路由失败：未找到 PromptServer 实例")
        return

    locale_routes = web.RouteTableDef()
    # 获取语言文件列表
    locale_routes.get("/ps-comfy-tiheaven-locales")(list_locales)
    locale_routes.get("/ps-comfy-tiheaven-locales/")(list_locales)
    # 获取指定语言文件内容
    locale_routes.get(r"/ps-comfy-tiheaven-locales/{filename:.*\.json}")(handle_get_locale)

    server.app.router.add_routes(locale_routes)
    #logger.info("语言文件路由已成功注册")

# 初始化时自动注册路由
register_workflow_routes()
register_locale_routes()
register_appinfo_route()

logger.info(f"[Ps-Comfy-TiHeaveN]: If you see me, it means the loading has been successfully completed, Please download the Photoshop plugin from https://github.com/tiheaven/Ps-Comfy-TiHeaveN-CustomNodes/releases")

# 不注册任何节点（必须留空）
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}