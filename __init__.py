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
            #logger.info(f"发现被忽略的节点，ID: {node_id}, 类型: {node.get('type')}")
        else:
            normal_node_ids.add(node_id)

    # 辅助函数：根据输出类型匹配被忽略节点的对应输入
    def get_corresponding_input(ignored_node, output_type):
        """根据输出类型找到被忽略节点中对应的输入（如IMAGE输出对应image输入）"""
        # 常见输出类型与输入名称的映射（可根据实际节点类型扩展）
        # type_mapping = {
        #     "IMAGE": "image",
        #     "UPSCALE_MODEL": "upscale_model"
        # }
        # input_name = type_mapping.get(output_type, None)

        # if input_name:
        #     # 查找名称匹配的输入
        #     for input_data in ignored_node.get("inputs", []):
        #         if input_data["name"] == input_name:
        #             return input_data
        # 若未匹配到，默认返回第一个带链接的输入（兼容其他类型）
        for input_data in ignored_node.get("inputs", []):
            if input_data.get("link") is not None:
                return input_data
        return None

    # 辅助函数：递归找到上游第一个正常节点（穿透被忽略节点）
    def find_original_source(link_id):
        """根据链接ID查找最终的正常节点源（跳过所有被忽略节点）"""
        # 找到链接对应的源节点信息和输出类型
        for link in links:
            if link[0] == link_id:  # link[0]是link_id
                source_node_id = str(link[1])  # 源节点ID
                source_out_idx = link[2]       # 源节点输出索引
                output_type = link[5]          # 链接类型（即源节点输出类型）

                # 如果源节点是正常节点，直接返回
                if source_node_id in normal_node_ids:
                    return [source_node_id, source_out_idx]
                # 如果源节点是被忽略节点，递归查找其对应输入的源
                elif source_node_id in ignore_node_ids:
                    # 找到被忽略节点
                    source_node = next((n for n in nodes if str(n["id"]) == source_node_id), None)
                    if not source_node:
                        return None
                    # 根据输出类型找到被忽略节点对应的输入
                    corresponding_input = get_corresponding_input(source_node, output_type)
                    if corresponding_input and corresponding_input.get("link") is not None:
                        return find_original_source(corresponding_input["link"])
        return None

    # 第二步：只添加正常节点（mode≠4），并处理输入链接、title、localized_names和types
    for node in nodes:
        node_id = str(node["id"])
        if node_id in ignore_node_ids:
            continue  # 跳过被忽略节点

        class_type = node["type"]
        # 处理title：优先取node的title，无则取"Node name for S&R"
        node_title = node.get("title")
        if node_title is None:
            # 从properties中获取备用名称
            properties = node.get("properties", {})
            node_title = properties.get("Node name for S&R", "")
            if not node_title:
                node_title = class_type
        
        inputs = {}
        # 初始化localized_names和types集合
        localized_names = []
        types = []
        
        # 收集带widget的输入名称（按输入顺序）
        widget_input_names = [
            inp["name"] for inp in node.get("inputs", [])
            if inp.get("widget") is not None  # 只包含有widget的输入
        ]
        
        # 过滤widgets_values中的"randomize"
        strings_to_filter = {"randomize", "fixed", "increment", "decrement"}
        filtered_widget_values = [
            v for v in node.get("widgets_values", []) 
            if v not in strings_to_filter  # 同时排除集合中的所有字符串
        ]
        
        # 检查是否有sampler_name和scheduler输入
        has_sampler_name = any(inp["name"] == "sampler_name" for inp in node.get("inputs", []))
        has_scheduler = any(inp["name"] == "scheduler" for inp in node.get("inputs", []))
        
        for input_data in node.get("inputs", []):
            input_name = input_data["name"]
            
            # 处理localized_names：收集name与label/localized_name的映射
            label = input_data.get("label")
            localized_name = input_data.get("localized_name")
            # 优先取label，无label则取localized_name
            display_value = label if label is not None else localized_name
            if display_value is not None:
                localized_names.append({input_name: display_value})
            
            # 处理types：收集带widget的输入的name与type的映射
            if input_data.get("widget") is not None:
                input_type = input_data.get("type")
                if input_type is not None:
                    types.append({input_name: input_type})
            
            # 处理输入链接或widget值
            if input_data.get("link") is not None:
                # 递归找到最终的正常节点源
                original_source = find_original_source(input_data["link"])
                if original_source:
                    inputs[input_name] = original_source
                else:
                    logger.warning(f"节点 {node_id} 的输入 {input_name} 无法找到有效源，已忽略")
            else:
                # 处理widgets值（使用过滤后的值）
                if input_data.get("widget") is not None:  # 确保是带widget的输入
                    try:
                        # 从带widget的输入名称列表中获取索引
                        widget_index = widget_input_names.index(input_name)
                        # 从过滤后的值中取值（确保索引有效）
                        if widget_index < len(filtered_widget_values):
                            inputs[input_name] = filtered_widget_values[widget_index]
                        else:
                            logger.warning(f"节点 {node_id} 的输入 {input_name} 没有对应的widget值（过滤后）")
                    except ValueError as e:
                        logger.warning(f"节点 {node_id} 的输入 {input_name} 不在widget输入列表中: {e}")
        
        # 构建_meta信息（包含title）
        _meta = {"title": node_title} if node_title else {}
        
        # 处理SAMPLERS和SCHEDULERS
        if has_sampler_name:
            inputs["_samplers"] = comfy.samplers.KSampler.SAMPLERS
        if has_scheduler:
            inputs["_schedulers"] = comfy.samplers.KSampler.SCHEDULERS
        
        # 将localized_names和types暂存到inputs中，后续移至顶层
        inputs["_localized_names"] = localized_names
        inputs["_types"] = types
        
        # 添加节点到构建器，将_meta暂存到inputs中
        builder.node(class_type, id=node_id, _meta=_meta, **inputs)

    # 第三步：调整_meta、localized_names、types、SAMPLERS和SCHEDULERS的位置，使其与class_type同级
    finalized = builder.finalize()
    for node_id, node_data in finalized.items():
        # 从inputs中取出_meta并移到顶层
        if "_meta" in node_data["inputs"]:
            node_data["_meta"] = node_data["inputs"].pop("_meta")
        # 从inputs中取出localized_names并移到顶层
        if "_localized_names" in node_data["inputs"]:
            node_data["localized_names"] = node_data["inputs"].pop("_localized_names")
        # 从inputs中取出types并移到顶层
        if "_types" in node_data["inputs"]:
            node_data["types"] = node_data["inputs"].pop("_types")
        # 从inputs中取出SAMPLERS并移到顶层
        if "_samplers" in node_data["inputs"]:
            node_data["samplers"] = node_data["inputs"].pop("_samplers")
        # 从inputs中取出SCHEDULERS并移到顶层
        if "_schedulers" in node_data["inputs"]:
            node_data["schedulers"] = node_data["inputs"].pop("_schedulers")
    
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

logger.info(f"[Ps-Comfy-TiHeaveN]: If you see me, it means the loading has been successfully completed, Please download the Photoshop plugin from https://github.com/tiheaven/Ps-Comfy-TiHeaveN-CustomNodes/releases")

# 不注册任何节点（必须留空）
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}