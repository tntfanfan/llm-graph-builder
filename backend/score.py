from fastapi import FastAPI, File, UploadFile, Form, Request, HTTPException
from fastapi_health import health
from fastapi.middleware.cors import CORSMiddleware
from src.main import *
from src.QA_integration import *
from src.shared.common_fn import *
from src.shared.llm_graph_builder_exception import LLMGraphBuilderException
import uvicorn
import asyncio
import base64
from langserve import add_routes
from langchain_google_vertexai import ChatVertexAI
from src.api_response import create_api_response
from src.graphDB_dataAccess import graphDBdataAccess
from src.graph_query import get_graph_results,get_chunktext_results,visualize_schema
from src.chunkid_entities import get_entities_from_chunkids
from src.post_processing import create_vector_fulltext_indexes, create_entity_embedding, graph_schema_consolidation
from sse_starlette.sse import EventSourceResponse
from src.communities import create_communities
from src.neighbours import get_neighbour_nodes
import json
from typing import List, Optional
from google.oauth2.credentials import Credentials
import os
from src.logger import CustomLogger
from datetime import datetime, timezone
import time
import gc
from Secweb.XContentTypeOptions import XContentTypeOptions
from Secweb.XFrameOptions import XFrame
from fastapi.middleware.gzip import GZipMiddleware
from src.ragas_eval import *
from starlette.types import ASGIApp, Receive, Scope, Send
from langchain_neo4j import Neo4jGraph
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from dotenv import load_dotenv
load_dotenv(override=True)

logger = CustomLogger()
CHUNK_DIR = os.path.join(os.path.dirname(__file__), "chunks")
MERGED_DIR = os.path.join(os.path.dirname(__file__), "merged_files")

def sanitize_filename(filename):
   """
   Sanitize the user-provided filename to prevent directory traversal and remove unsafe characters.
   """
   # Remove path separators and collapse redundant separators
   filename = os.path.basename(filename)
   filename = os.path.normpath(filename)
   return filename

def validate_file_path(directory, filename):
   """
   Construct the full file path and ensure it is within the specified directory.
   """
   file_path = os.path.join(directory, filename)
   abs_directory = os.path.abspath(directory)
   abs_file_path = os.path.abspath(file_path)
   # Ensure the file path starts with the intended directory path
   if not abs_file_path.startswith(abs_directory):
       raise ValueError("Invalid file path")
   return abs_file_path

def healthy_condition():
    output = {"healthy": True}
    return output

def healthy():
    return True

def sick():
    return False
class CustomGZipMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        paths: List[str],
        minimum_size: int = 1000,
        compresslevel: int = 5
    ):
        self.app = app
        self.paths = paths
        self.minimum_size = minimum_size
        self.compresslevel = compresslevel
    
    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
 
        path = scope["path"]
        should_compress = any(path.startswith(gzip_path) for gzip_path in self.paths)
        
        if not should_compress:
            return await self.app(scope, receive, send)
        
        gzip_middleware = GZipMiddleware(
            app=self.app,
            minimum_size=self.minimum_size,
            compresslevel=self.compresslevel
        )
        await gzip_middleware(scope, receive, send)
app = FastAPI()
app.add_middleware(XContentTypeOptions)
app.add_middleware(XFrame, Option={'X-Frame-Options': 'DENY'})
app.add_middleware(CustomGZipMiddleware, minimum_size=1000, compresslevel=5,paths=["/sources_list","/url/scan","/extract","/chat_bot","/chunk_entities","/get_neighbours","/graph_query","/schema","/populate_graph_schema","/get_unconnected_nodes_list","/get_duplicate_nodes","/fetch_chunktext","/schema_visualization"])
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SessionMiddleware, secret_key=os.urandom(24))

is_gemini_enabled = os.environ.get("GEMINI_ENABLED", "False").lower() in ("true", "1", "yes")
if is_gemini_enabled:
    add_routes(app,ChatVertexAI(), path="/vertexai")

app.add_api_route("/health", health([healthy_condition, healthy]))



@app.post("/url/scan")
async def create_source_knowledge_graph_url(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    source_url=Form(None),
    database=Form(None),
    aws_access_key_id=Form(None),
    aws_secret_access_key=Form(None),
    wiki_query=Form(None),
    model=Form(),
    gcs_bucket_name=Form(None),
    gcs_bucket_folder=Form(None),
    source_type=Form(None),
    gcs_project_id=Form(None),
    access_token=Form(None),
    email=Form(None)
    ):
    
    try:
        start = time.time()
        if source_url is not None:
            source = source_url
        else:
            source = wiki_query
            
        graph = create_graph_database_connection(uri, userName, password, database)
        if source_type == 's3 bucket' and aws_access_key_id and aws_secret_access_key:
            lst_file_name,success_count,failed_count = await asyncio.to_thread(create_source_node_graph_url_s3,graph, model, source_url, aws_access_key_id, aws_secret_access_key, source_type
            )
        elif source_type == 'gcs bucket':
            lst_file_name,success_count,failed_count = create_source_node_graph_url_gcs(graph, model, gcs_project_id, gcs_bucket_name, gcs_bucket_folder, source_type,Credentials(access_token)
            )
        elif source_type == 'web-url':
            lst_file_name,success_count,failed_count = await asyncio.to_thread(create_source_node_graph_web_url,graph, model, source_url, source_type
            )  
        elif source_type == 'youtube':
            lst_file_name,success_count,failed_count = await asyncio.to_thread(create_source_node_graph_url_youtube,graph, model, source_url, source_type
            )
        elif source_type == 'Wikipedia':
            lst_file_name,success_count,failed_count = await asyncio.to_thread(create_source_node_graph_url_wikipedia,graph, model, wiki_query, source_type
            )
        else:
            return create_api_response('Failed',message='source_type is other than accepted source')

        message = f"Source Node created successfully for source type: {source_type} and source: {source}"
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'url_scan','db_url':uri,'url_scanned_file':lst_file_name, 'source_url':source_url, 'wiki_query':wiki_query, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','userName':userName, 'database':database, 'aws_access_key_id':aws_access_key_id,
                            'model':model, 'gcs_bucket_name':gcs_bucket_name, 'gcs_bucket_folder':gcs_bucket_folder, 'source_type':source_type,
                            'gcs_project_id':gcs_project_id, 'logging_time': formatted_time(datetime.now(timezone.utc)),'email':email}
        logger.log_struct(json_obj, "INFO")
        result ={'elapsed_api_time' : f'{elapsed_time:.2f}'}
        return create_api_response("Success",message=message,success_count=success_count,failed_count=failed_count,file_name=lst_file_name,data=result)
    except LLMGraphBuilderException as e:
        error_message = str(e)
        message = f" Unable to create source node for source type: {source_type} and source: {source}"
        # Set the status "Success" becuase we are treating these error already handled by application as like custom errors.
        json_obj = {'error_message':error_message, 'status':'Success','db_url':uri, 'userName':userName, 'database':database,'success_count':1, 'source_type': source_type, 'source_url':source_url, 'wiki_query':wiki_query, 'logging_time': formatted_time(datetime.now(timezone.utc)),'email':email}
        logger.log_struct(json_obj, "INFO")
        logging.exception(f'File Failed in upload: {e}')
        return create_api_response('Failed',message=message + error_message[:80],error=error_message,file_source=source_type)
    except Exception as e:
        error_message = str(e)
        message = f" Unable to create source node for source type: {source_type} and source: {source}"
        json_obj = {'error_message':error_message, 'status':'Failed','db_url':uri, 'userName':userName, 'database':database,'failed_count':1, 'source_type': source_type, 'source_url':source_url, 'wiki_query':wiki_query, 'logging_time': formatted_time(datetime.now(timezone.utc)),'email':email}
        logger.log_struct(json_obj, "ERROR")
        logging.exception(f'Exception Stack trace upload:{e}')
        return create_api_response('Failed',message=message + error_message[:80],error=error_message,file_source=source_type)
    finally:
        gc.collect()

@app.post("/extract")
async def extract_knowledge_graph_from_file(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    model=Form(),
    database=Form(None),
    source_url=Form(None),
    aws_access_key_id=Form(None),
    aws_secret_access_key=Form(None),
    wiki_query=Form(None),
    gcs_project_id=Form(None),
    gcs_bucket_name=Form(None),
    gcs_bucket_folder=Form(None),
    gcs_blob_filename=Form(None),
    source_type=Form(None),
    file_name=Form(None),
    allowedNodes=Form(None),
    allowedRelationship=Form(None),
    node_properties=Form(None),
    relationship_properties=Form(None), 
    token_chunk_size: Optional[int] = Form(None),
    chunk_overlap: Optional[int] = Form(None),
    chunks_to_combine: Optional[int] = Form(None),
    language=Form(None),
    access_token=Form(None),
    retry_condition=Form(None),
    additional_instructions=Form(None),
    email=Form(None)
):
    """
    Calls 'extract_graph_from_file' in a new thread to create Neo4jGraph from a
    PDF file based on the model.

    Args:
          uri: URI of the graph to extract
          userName: Username to use for graph creation
          password: Password to use for graph creation
          file: File object containing the PDF file
          model: Type of model to use ('Diffbot'or'OpenAI GPT')

    Returns:
          Nodes and Relations created in Neo4j databse for the pdf file
    """
    try:
        start_time = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)   
        graphDb_data_Access = graphDBdataAccess(graph)
        if source_type == 'local file':
            file_name = sanitize_filename(file_name)
            merged_file_path = validate_file_path(MERGED_DIR, file_name)
            uri_latency, result = await extract_graph_from_file_local_file(uri, userName, password, database, model, merged_file_path, file_name, allowedNodes, allowedRelationship, node_properties, relationship_properties, token_chunk_size, chunk_overlap, chunks_to_combine, retry_condition, additional_instructions)

        elif source_type == 's3 bucket' and source_url:
            uri_latency, result = await extract_graph_from_file_s3(uri, userName, password, database, model, source_url, aws_access_key_id, aws_secret_access_key, file_name, allowedNodes, allowedRelationship, node_properties, relationship_properties, token_chunk_size, chunk_overlap, chunks_to_combine, retry_condition, additional_instructions)
        
        elif source_type == 'web-url':
            uri_latency, result = await extract_graph_from_web_page(uri, userName, password, database, model, source_url, file_name, allowedNodes, allowedRelationship, node_properties, relationship_properties, token_chunk_size, chunk_overlap, chunks_to_combine, retry_condition, additional_instructions)

        elif source_type == 'youtube' and source_url:
            uri_latency, result = await extract_graph_from_file_youtube(uri, userName, password, database, model, source_url, file_name, allowedNodes, allowedRelationship, node_properties, relationship_properties, token_chunk_size, chunk_overlap, chunks_to_combine, retry_condition, additional_instructions)

        elif source_type == 'Wikipedia' and wiki_query:
            uri_latency, result = await extract_graph_from_file_Wikipedia(uri, userName, password, database, model, wiki_query, language, file_name, allowedNodes, allowedRelationship, node_properties, relationship_properties, token_chunk_size, chunk_overlap, chunks_to_combine, retry_condition, additional_instructions)

        elif source_type == 'gcs bucket' and gcs_bucket_name:
            uri_latency, result = await extract_graph_from_file_gcs(uri, userName, password, database, model, gcs_project_id, gcs_bucket_name, gcs_bucket_folder, gcs_blob_filename, access_token, file_name, allowedNodes, allowedRelationship, node_properties, relationship_properties, token_chunk_size, chunk_overlap, chunks_to_combine, retry_condition, additional_instructions)
        else:
            return create_api_response('Failed',message='source_type is other than accepted source')
        extract_api_time = time.time() - start_time
        if result is not None:
            logging.info("Going for counting nodes and relationships in extract")
            count_node_time = time.time()
            graph = create_graph_database_connection(uri, userName, password, database)   
            graphDb_data_Access = graphDBdataAccess(graph)
            count_response = graphDb_data_Access.update_node_relationship_count(file_name)
            logging.info("Nodes and Relationship Counts updated")
            if count_response :
                result['chunkNodeCount'] = count_response[file_name].get('chunkNodeCount',"0")
                result['chunkRelCount'] =  count_response[file_name].get('chunkRelCount',"0")
                result['entityNodeCount']=  count_response[file_name].get('entityNodeCount',"0")
                result['entityEntityRelCount']=  count_response[file_name].get('entityEntityRelCount',"0")
                result['communityNodeCount']=  count_response[file_name].get('communityNodeCount',"0")
                result['communityRelCount']= count_response[file_name].get('communityRelCount',"0")
                result['nodeCount'] = count_response[file_name].get('nodeCount',"0")
                result['relationshipCount']  = count_response[file_name].get('relationshipCount',"0")
                logging.info(f"counting completed in {(time.time()-count_node_time):.2f}")
            result['db_url'] = uri
            result['api_name'] = 'extract'
            result['source_url'] = source_url
            result['wiki_query'] = wiki_query
            result['source_type'] = source_type
            result['logging_time'] = formatted_time(datetime.now(timezone.utc))
            result['elapsed_api_time'] = f'{extract_api_time:.2f}'
            result['userName'] = userName
            result['database'] = database
            result['aws_access_key_id'] = aws_access_key_id
            result['gcs_bucket_name'] = gcs_bucket_name
            result['gcs_bucket_folder'] = gcs_bucket_folder
            result['gcs_blob_filename'] = gcs_blob_filename
            result['gcs_project_id'] = gcs_project_id
            result['language'] = language
            result['retry_condition'] = retry_condition
            result['email'] = email
        logger.log_struct(result, "INFO")
        result.update(uri_latency)
        logging.info(f"extraction completed in {extract_api_time:.2f} seconds for file name {file_name}")
        return create_api_response('Success', data=result, file_source= source_type)
    except LLMGraphBuilderException as e:
        error_message = str(e)
        graph = create_graph_database_connection(uri, userName, password, database)   
        graphDb_data_Access = graphDBdataAccess(graph)
        graphDb_data_Access.update_exception_db(file_name,error_message, retry_condition)
        if source_type == 'local file':
            failed_file_process(uri,file_name, merged_file_path)
        node_detail = graphDb_data_Access.get_current_status_document_node(file_name)
        # Set the status "Completed" in logging becuase we are treating these error already handled by application as like custom errors.
        json_obj = {'api_name':'extract','message':error_message,'file_created_at':formatted_time(node_detail[0]['created_time']),'error_message':error_message, 'file_name': file_name,'status':'Completed',
                    'db_url':uri, 'userName':userName, 'database':database,'success_count':1, 'source_type': source_type, 'source_url':source_url, 'wiki_query':wiki_query, 'logging_time': formatted_time(datetime.now(timezone.utc)),'email':email,
                    'allowedNodes': allowedNodes, 'allowedRelationship': allowedRelationship}
        logger.log_struct(json_obj, "INFO")
        logging.exception(f'File Failed in extraction: {e}')
        return create_api_response("Failed", message = error_message, error=error_message, file_name=file_name)
    except Exception as e:
        message=f"Failed To Process File:{file_name} or LLM Unable To Parse Content "
        error_message = str(e)
        graph = create_graph_database_connection(uri, userName, password, database)   
        graphDb_data_Access = graphDBdataAccess(graph)
        graphDb_data_Access.update_exception_db(file_name,error_message, retry_condition)
        if source_type == 'local file':
            failed_file_process(uri,file_name, merged_file_path)
        node_detail = graphDb_data_Access.get_current_status_document_node(file_name)
        
        json_obj = {'api_name':'extract','message':message,'file_created_at':formatted_time(node_detail[0]['created_time']),'error_message':error_message, 'file_name': file_name,'status':'Failed',
                    'db_url':uri, 'userName':userName, 'database':database,'failed_count':1, 'source_type': source_type, 'source_url':source_url, 'wiki_query':wiki_query, 'logging_time': formatted_time(datetime.now(timezone.utc)),'email':email,
                    'allowedNodes': allowedNodes, 'allowedRelationship': allowedRelationship}
        logger.log_struct(json_obj, "ERROR")
        logging.exception(f'File Failed in extraction: {e}')
        return create_api_response('Failed', message=message + error_message[:100], error=error_message, file_name = file_name)
    finally:
        gc.collect()
            
@app.post("/sources_list")
async def get_source_list(
    uri=Form(None),
    userName=Form(None),
    password=Form(None),
    database=Form(None),
    email=Form(None)):
    """
    Calls 'get_source_list_from_graph' which returns list of sources which already exist in databse
    """
    try:
        start = time.time()
        result = await asyncio.to_thread(get_source_list_from_graph,uri,userName,password,database)
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'sources_list','db_url':uri, 'userName':userName, 'database':database, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response("Success",data=result, message=f"Total elapsed API time {elapsed_time:.2f}")
    except Exception as e:
        job_status = "Failed"
        message="Unable to fetch source list"
        error_message = str(e)
        logging.exception(f'Exception:{error_message}')
        return create_api_response(job_status, message=message, error=error_message)

@app.post("/post_processing")
async def post_processing(uri=Form(None), userName=Form(None), password=Form(None), database=Form(None), tasks=Form(None), email=Form(None)):
    try:
        graph = create_graph_database_connection(uri, userName, password, database)
        tasks = set(map(str.strip, json.loads(tasks)))
        api_name = 'post_processing'
        count_response = []
        start = time.time()
        if "materialize_text_chunk_similarities" in tasks:
            await asyncio.to_thread(update_graph, graph)
            api_name = 'post_processing/update_similarity_graph'
            logging.info(f'Updated KNN Graph')

        if "enable_hybrid_search_and_fulltext_search_in_bloom" in tasks:
            await asyncio.to_thread(create_vector_fulltext_indexes, uri=uri, username=userName, password=password, database=database)
            api_name = 'post_processing/enable_hybrid_search_and_fulltext_search_in_bloom'
            logging.info(f'Full Text index created')

        if os.environ.get('ENTITY_EMBEDDING','False').upper()=="TRUE" and "materialize_entity_similarities" in tasks:
            await asyncio.to_thread(create_entity_embedding, graph)
            api_name = 'post_processing/create_entity_embedding'
            logging.info(f'Entity Embeddings created')

        if "graph_schema_consolidation" in tasks :
            await asyncio.to_thread(graph_schema_consolidation, graph)
            api_name = 'post_processing/graph_schema_consolidation'
            logging.info(f'Updated nodes and relationship labels')
            
        if "enable_communities" in tasks:
            api_name = 'create_communities'
            await asyncio.to_thread(create_communities, uri, userName, password, database)  
            
            logging.info(f'created communities')
        graph = create_graph_database_connection(uri, userName, password, database)   
        graphDb_data_Access = graphDBdataAccess(graph)
        document_name = ""
        count_response = graphDb_data_Access.update_node_relationship_count(document_name)
        if count_response:
            count_response = [{"filename": filename, **counts} for filename, counts in count_response.items()]
            logging.info(f'Updated source node with community related counts')
        
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name': api_name, 'db_url': uri, 'userName':userName, 'database':database, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj)
        return create_api_response('Success', data=count_response, message='All tasks completed successfully')
    
    except Exception as e:
        job_status = "Failed"
        error_message = str(e)
        message = f"Unable to complete tasks"
        logging.exception(f'Exception in post_processing tasks: {error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    
    finally:
        gc.collect()
                
@app.post("/chat_bot")
async def chat_bot(uri=Form(None),model=Form(None),userName=Form(None), password=Form(None), database=Form(None),question=Form(None), document_names=Form(None),session_id=Form(None),mode=Form(None),email=Form(None)):
    logging.info(f"QA_RAG called at {datetime.now()}")
    qa_rag_start_time = time.time()
    try:
        if mode == "graph":
            graph = Neo4jGraph( url=uri,username=userName,password=password,database=database,sanitize = True, refresh_schema=True)
        else:
            graph = create_graph_database_connection(uri, userName, password, database)
        
        graph_DB_dataAccess = graphDBdataAccess(graph)
        write_access = graph_DB_dataAccess.check_account_access(database=database)
        result = await asyncio.to_thread(QA_RAG,graph=graph,model=model,question=question,document_names=document_names,session_id=session_id,mode=mode,write_access=write_access)

        total_call_time = time.time() - qa_rag_start_time
        logging.info(f"Total Response time is  {total_call_time:.2f} seconds")
        result["info"]["response_time"] = round(total_call_time, 2)
        
        json_obj = {'api_name':'chat_bot','db_url':uri, 'userName':userName, 'database':database, 'question':question,'document_names':document_names,
                             'session_id':session_id, 'mode':mode, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{total_call_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        
        return create_api_response('Success',data=result)
    except Exception as e:
        job_status = "Failed"
        message="Unable to get chat response"
        error_message = str(e)
        logging.exception(f'Exception in chat bot:{error_message}')
        return create_api_response(job_status, message=message, error=error_message,data=mode)
    finally:
        gc.collect()

@app.post("/chunk_entities")
async def chunk_entities(uri=Form(None),userName=Form(None), password=Form(None), database=Form(None), nodedetails=Form(None),entities=Form(),mode=Form(),email=Form(None)):
    try:
        start = time.time()
        result = await asyncio.to_thread(get_entities_from_chunkids,nodedetails=nodedetails,entities=entities,mode=mode,uri=uri, username=userName, password=password, database=database)
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'chunk_entities','db_url':uri, 'userName':userName, 'database':database, 'nodedetails':nodedetails,'entities':entities,
                            'mode':mode, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success',data=result,message=f"Total elapsed API time {elapsed_time:.2f}")
    except Exception as e:
        job_status = "Failed"
        message="Unable to extract entities from chunk ids"
        error_message = str(e)
        logging.exception(f'Exception in chat bot:{error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()

@app.post("/get_neighbours")
async def get_neighbours(uri=Form(None),userName=Form(None), password=Form(None), database=Form(None), elementId=Form(None),email=Form(None)):
    try:
        start = time.time()
        result = await asyncio.to_thread(get_neighbour_nodes,uri=uri, username=userName, password=password,database=database, element_id=elementId)
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'get_neighbours', 'userName':userName, 'database':database,'db_url':uri, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success',data=result,message=f"Total elapsed API time {elapsed_time:.2f}")
    except Exception as e:
        job_status = "Failed"
        message="Unable to extract neighbour nodes for given element ID"
        error_message = str(e)
        logging.exception(f'Exception in get neighbours :{error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()

@app.post("/graph_query")
async def graph_query(
    uri: str = Form(None),
    database: str = Form(None),
    userName: str = Form(None),
    password: str = Form(None),
    document_names: str = Form(None),
    email=Form(None)
):
    try:
        start = time.time()
        result = await asyncio.to_thread(
            get_graph_results,
            uri=uri,
            username=userName,
            password=password,
            database=database,
            document_names=document_names
        )
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'graph_query','db_url':uri, 'userName':userName, 'database':database, 'document_names':document_names, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success', data=result,message=f"Total elapsed API time {elapsed_time:.2f}")
    except Exception as e:
        job_status = "Failed"
        message = "Unable to get graph query response"
        error_message = str(e)
        logging.exception(f'Exception in graph query: {error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()
    

@app.post("/clear_chat_bot")
async def clear_chat_bot(uri=Form(None),userName=Form(None), password=Form(None), database=Form(None), session_id=Form(None),email=Form(None)):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        result = await asyncio.to_thread(clear_chat_history,graph=graph,session_id=session_id)
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'clear_chat_bot', 'db_url':uri, 'userName':userName, 'database':database, 'session_id':session_id, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success',data=result)
    except Exception as e:
        job_status = "Failed"
        message="Unable to clear chat History"
        error_message = str(e)
        logging.exception(f'Exception in chat bot:{error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()
            
@app.post("/connect")
async def connect(uri=Form(None), userName=Form(None), password=Form(None), database=Form(None),email=Form(None)):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        result = await asyncio.to_thread(connection_check_and_get_vector_dimensions, graph, database)
        gcs_file_cache = os.environ.get('GCS_FILE_CACHE')
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'connect','db_url':uri, 'userName':userName, 'database':database, 'count':1, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        result['elapsed_api_time'] = f'{elapsed_time:.2f}'
        result['gcs_file_cache'] = gcs_file_cache
        return create_api_response('Success',data=result)
    except Exception as e:
        job_status = "Failed"
        message="Connection failed to connect Neo4j database"
        error_message = str(e)
        logging.exception(f'Connection failed to connect Neo4j database:{error_message}')
        return create_api_response(job_status, message=message, error=error_message)

@app.post("/upload")
async def upload_large_file_into_chunks(file:UploadFile = File(...), chunkNumber=Form(None), totalChunks=Form(None), 
                                        originalname=Form(None), model=Form(None), uri=Form(None), userName=Form(None), 
                                        password=Form(None), database=Form(None),email=Form(None)):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        result = await asyncio.to_thread(upload_file, graph, model, file, chunkNumber, totalChunks, originalname, uri, CHUNK_DIR, MERGED_DIR)
        end = time.time()
        elapsed_time = end - start
        if int(chunkNumber) == int(totalChunks):
            json_obj = {'api_name':'upload','db_url':uri,'userName':userName, 'database':database, 'chunkNumber':chunkNumber,'totalChunks':totalChunks,
                                'original_file_name':originalname,'model':model, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
            logger.log_struct(json_obj, "INFO")
        if int(chunkNumber) == int(totalChunks):
            return create_api_response('Success',data=result, message='Source Node Created Successfully')
        else:
            return create_api_response('Success', message=result)
    except Exception as e:
        message="Unable to upload file in chunks"
        error_message = str(e)
        graph = create_graph_database_connection(uri, userName, password, database)   
        graphDb_data_Access = graphDBdataAccess(graph)
        graphDb_data_Access.update_exception_db(originalname,error_message)
        logging.info(message)
        logging.exception(f'Exception:{error_message}')
        return create_api_response('Failed', message=message + error_message[:100], error=error_message, file_name = originalname)
    finally:
        gc.collect()
            
@app.post("/schema")
async def get_structured_schema(uri=Form(None), userName=Form(None), password=Form(None), database=Form(None),email=Form(None)):
    try:
        start = time.time()
        result = await asyncio.to_thread(get_labels_and_relationtypes, uri, userName, password, database)
        end = time.time()
        elapsed_time = end - start
        logging.info(f'Schema result from DB: {result}')
        json_obj = {'api_name':'schema','db_url':uri, 'userName':userName, 'database':database, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success', data=result,message=f"Total elapsed API time {elapsed_time:.2f}")
    except Exception as e:
        message="Unable to get the labels and relationtypes from neo4j database"
        error_message = str(e)
        logging.info(message)
        logging.exception(f'Exception:{error_message}')
        return create_api_response("Failed", message=message, error=error_message)
    finally:
        gc.collect()
            
def decode_password(pwd):
    sample_string_bytes = base64.b64decode(pwd)
    decoded_password = sample_string_bytes.decode("utf-8")
    return decoded_password

def encode_password(pwd):
    data_bytes = pwd.encode('ascii')
    encoded_pwd_bytes = base64.b64encode(data_bytes)
    return encoded_pwd_bytes

@app.get("/update_extract_status/{file_name}")
async def update_extract_status(request: Request, file_name: str, uri:str=None, userName:str=None, password:str=None, database:str=None):
    async def generate():
        status = ''
        
        if password is not None and password != "null":
            decoded_password = decode_password(password)
        else:
            decoded_password = None

        url = uri
        if url and " " in url:
            url= url.replace(" ","+")
            
        graph = create_graph_database_connection(url, userName, decoded_password, database)
        graphDb_data_Access = graphDBdataAccess(graph)
        while True:
            try:
                if await request.is_disconnected():
                    logging.info(" SSE Client disconnected")
                    break
                # get the current status of document node
                
                else:
                    result = graphDb_data_Access.get_current_status_document_node(file_name)
                    if len(result) > 0:
                        status = json.dumps({'fileName':file_name, 
                        'status':result[0]['Status'],
                        'processingTime':result[0]['processingTime'],
                        'nodeCount':result[0]['nodeCount'],
                        'relationshipCount':result[0]['relationshipCount'],
                        'model':result[0]['model'],
                        'total_chunks':result[0]['total_chunks'],
                        'fileSize':result[0]['fileSize'],
                        'processed_chunk':result[0]['processed_chunk'],
                        'fileSource':result[0]['fileSource'],
                        'chunkNodeCount' : result[0]['chunkNodeCount'],
                        'chunkRelCount' : result[0]['chunkRelCount'],
                        'entityNodeCount' : result[0]['entityNodeCount'],
                        'entityEntityRelCount' : result[0]['entityEntityRelCount'],
                        'communityNodeCount' : result[0]['communityNodeCount'],
                        'communityRelCount' : result[0]['communityRelCount']
                        })
                    yield status
            except asyncio.CancelledError:
                logging.info("SSE Connection cancelled")
    
    return EventSourceResponse(generate(),ping=60)

@app.post("/delete_document_and_entities")
async def delete_document_and_entities(uri=Form(None), 
                                       userName=Form(None), 
                                       password=Form(None), 
                                       database=Form(None), 
                                       filenames=Form(),
                                       source_types=Form(),
                                       deleteEntities=Form(),
                                       email=Form(None)):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        graphDb_data_Access = graphDBdataAccess(graph)
        files_list_size = await asyncio.to_thread(graphDb_data_Access.delete_file_from_graph, filenames, source_types, deleteEntities, MERGED_DIR, uri)
        message = f"Deleted {files_list_size} documents with entities from database"
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'delete_document_and_entities','db_url':uri, 'userName':userName, 'database':database, 'filenames':filenames,'deleteEntities':deleteEntities,
                            'source_types':source_types, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success',message=message)
    except Exception as e:
        job_status = "Failed"
        message=f"Unable to delete document {filenames}"
        error_message = str(e)
        logging.exception(f'{message}:{error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()

@app.get('/document_status/{file_name}')
async def get_document_status(file_name, url, userName, password, database):
    decoded_password = decode_password(password)
   
    try:
        if " " in url:
            uri= url.replace(" ","+")
        else:
            uri=url
        graph = create_graph_database_connection(uri, userName, decoded_password, database)
        graphDb_data_Access = graphDBdataAccess(graph)
        result = graphDb_data_Access.get_current_status_document_node(file_name)
        if len(result) > 0:
            status = {'fileName':file_name, 
                'status':result[0]['Status'],
                'processingTime':result[0]['processingTime'],
                'nodeCount':result[0]['nodeCount'],
                'relationshipCount':result[0]['relationshipCount'],
                'model':result[0]['model'],
                'total_chunks':result[0]['total_chunks'],
                'fileSize':result[0]['fileSize'],
                'processed_chunk':result[0]['processed_chunk'],
                'fileSource':result[0]['fileSource'],
                'chunkNodeCount' : result[0]['chunkNodeCount'],
                'chunkRelCount' : result[0]['chunkRelCount'],
                'entityNodeCount' : result[0]['entityNodeCount'],
                'entityEntityRelCount' : result[0]['entityEntityRelCount'],
                'communityNodeCount' : result[0]['communityNodeCount'],
                'communityRelCount' : result[0]['communityRelCount']
                }
        else:
            status = {'fileName':file_name, 'status':'Failed'}
        logging.info(f'Result of document status in refresh : {result}')
        return create_api_response('Success',message="",file_name=status)
    except Exception as e:
        message=f"Unable to get the document status"
        error_message = str(e)
        logging.exception(f'{message}:{error_message}')
        return create_api_response('Failed',message=message)
    
@app.post("/cancelled_job")
async def cancelled_job(uri=Form(None), userName=Form(None), password=Form(None), database=Form(None), filenames=Form(None), source_types=Form(None),email=Form(None)):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        result = manually_cancelled_job(graph,filenames, source_types, MERGED_DIR, uri)
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'cancelled_job','db_url':uri, 'userName':userName, 'database':database, 'filenames':filenames,
                            'source_types':source_types, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success',message=result)
    except Exception as e:
        job_status = "Failed"
        message="Unable to cancelled the running job"
        error_message = str(e)
        logging.exception(f'Exception in cancelling the running job:{error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()

@app.post("/populate_graph_schema")
async def populate_graph_schema(input_text=Form(None), model=Form(None), is_schema_description_checked=Form(None),is_local_storage=Form(None),email=Form(None)):
    try:
        start = time.time()
        result = populate_graph_schema_from_text(input_text, model, is_schema_description_checked, is_local_storage)
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'populate_graph_schema', 'model':model, 'is_schema_description_checked':is_schema_description_checked, 'input_text':input_text, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success',data=result)
    except Exception as e:
        job_status = "Failed"
        message="Unable to get the schema from text"
        error_message = str(e)
        logging.exception(f'Exception in getting the schema from text:{error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()
        
@app.post("/get_unconnected_nodes_list")
async def get_unconnected_nodes_list(uri=Form(None), userName=Form(None), password=Form(None), database=Form(None),email=Form(None)):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        graphDb_data_Access = graphDBdataAccess(graph)
        nodes_list, total_nodes = graphDb_data_Access.list_unconnected_nodes()
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'get_unconnected_nodes_list','db_url':uri, 'userName':userName, 'database':database, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success',data=nodes_list,message=total_nodes)
    except Exception as e:
        job_status = "Failed"
        message="Unable to get the list of unconnected nodes"
        error_message = str(e)
        logging.exception(f'Exception in getting list of unconnected nodes:{error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()
        
@app.post("/delete_unconnected_nodes")
async def delete_orphan_nodes(uri=Form(None), userName=Form(None), password=Form(None), database=Form(None),unconnected_entities_list=Form(),email=Form(None)):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        graphDb_data_Access = graphDBdataAccess(graph)
        result = graphDb_data_Access.delete_unconnected_nodes(unconnected_entities_list)
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'delete_unconnected_nodes','db_url':uri, 'userName':userName, 'database':database,'unconnected_entities_list':unconnected_entities_list, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success',data=result,message="Unconnected entities delete successfully")
    except Exception as e:
        job_status = "Failed"
        message="Unable to delete the unconnected nodes"
        error_message = str(e)
        logging.exception(f'Exception in delete the unconnected nodes:{error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()
        
@app.post("/get_duplicate_nodes")
async def get_duplicate_nodes(uri=Form(None), userName=Form(None), password=Form(None), database=Form(None),email=Form(None)):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        graphDb_data_Access = graphDBdataAccess(graph)
        nodes_list, total_nodes = graphDb_data_Access.get_duplicate_nodes_list()
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'get_duplicate_nodes','db_url':uri,'userName':userName, 'database':database, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success',data=nodes_list, message=total_nodes)
    except Exception as e:
        job_status = "Failed"
        message="Unable to get the list of duplicate nodes"
        error_message = str(e)
        logging.exception(f'Exception in getting list of duplicate nodes:{error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()
        
@app.post("/merge_duplicate_nodes")
async def merge_duplicate_nodes(uri=Form(None), userName=Form(None), password=Form(None), database=Form(None),duplicate_nodes_list=Form(),email=Form(None)):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        graphDb_data_Access = graphDBdataAccess(graph)
        result = graphDb_data_Access.merge_duplicate_nodes(duplicate_nodes_list)
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'merge_duplicate_nodes','db_url':uri, 'userName':userName, 'database':database,
                            'duplicate_nodes_list':duplicate_nodes_list, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success',data=result,message="Duplicate entities merged successfully")
    except Exception as e:
        job_status = "Failed"
        message="Unable to merge the duplicate nodes"
        error_message = str(e)
        logging.exception(f'Exception in merge the duplicate nodes:{error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()
        
@app.post("/drop_create_vector_index")
async def drop_create_vector_index(uri=Form(None), userName=Form(None), password=Form(None), database=Form(None), isVectorIndexExist=Form(),email=Form(None)):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        graphDb_data_Access = graphDBdataAccess(graph)
        result = graphDb_data_Access.drop_create_vector_index(isVectorIndexExist)
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'drop_create_vector_index', 'db_url':uri, 'userName':userName, 'database':database,
                            'isVectorIndexExist':isVectorIndexExist, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success',message=result)
    except Exception as e:
        job_status = "Failed"
        message="Unable to drop and re-create vector index with correct dimesion as per application configuration"
        error_message = str(e)
        logging.exception(f'Exception into drop and re-create vector index with correct dimesion as per application configuration:{error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()
        
@app.post("/retry_processing")
async def retry_processing(uri=Form(None), userName=Form(None), password=Form(None), database=Form(None), file_name=Form(), retry_condition=Form(), email=Form(None)):
    try:
        start = time.time()
        graph = create_graph_database_connection(uri, userName, password, database)
        chunks = execute_graph_query(graph,QUERY_TO_GET_CHUNKS,params={"filename":file_name})
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'retry_processing', 'db_url':uri, 'userName':userName, 'database':database, 'file_name':file_name,'retry_condition':retry_condition,
                            'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}','email':email}
        logger.log_struct(json_obj, "INFO")
        if chunks[0]['text'] is None or chunks[0]['text']=="" or not chunks :
            return create_api_response('Success',message=f"Chunks are not created for the file{file_name}. Please upload again the file to re-process.",data=chunks)
        else:
            await asyncio.to_thread(set_status_retry, graph,file_name,retry_condition)
            return create_api_response('Success',message=f"Status set to Ready to Reprocess for filename : {file_name}")
    except Exception as e:
        job_status = "Failed"
        message="Unable to set status to Retry"
        error_message = str(e)
        logging.exception(f'{error_message}')
        return create_api_response(job_status, message=message, error=error_message)
    finally:
        gc.collect()    

@app.post('/metric')
async def calculate_metric(question: str = Form(),
                           context: str = Form(),
                           answer: str = Form(),
                           model: str = Form(),
                           mode: str = Form()):
    try:
        start = time.time()
        context_list = [str(item).strip() for item in json.loads(context)] if context else []
        answer_list = [str(item).strip() for item in json.loads(answer)] if answer else []
        mode_list = [str(item).strip() for item in json.loads(mode)] if mode else []

        result = await asyncio.to_thread(
            get_ragas_metrics, question, context_list, answer_list, model
        )
        if result is None or "error" in result:
            return create_api_response(
                'Failed',
                message='Failed to calculate evaluation metrics.',
                error=result.get("error", "Ragas evaluation returned null")
            )
        data = {mode: {metric: result[metric][i] for metric in result} for i, mode in enumerate(mode_list)}
        end = time.time()
        elapsed_time = end - start
        json_obj = {'api_name':'metric', 'question':question, 'context':context, 'answer':answer, 'model':model,'mode':mode,
                            'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}'}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success', data=data)
    except Exception as e:
        logging.exception(f"Error while calculating evaluation metrics: {e}")
        return create_api_response(
            'Failed',
            message="Error while calculating evaluation metrics",
            error=str(e)
        )
    finally:
        gc.collect()
       

@app.post('/additional_metrics')
async def calculate_additional_metrics(question: str = Form(),
                                        context: str = Form(),
                                        answer: str = Form(),
                                        reference: str = Form(),
                                        model: str = Form(),
                                        mode: str = Form(),
):
   try:
       context_list = [str(item).strip() for item in json.loads(context)] if context else []
       answer_list = [str(item).strip() for item in json.loads(answer)] if answer else []
       mode_list = [str(item).strip() for item in json.loads(mode)] if mode else []
       result = await get_additional_metrics(question, context_list,answer_list, reference, model)
       if result is None or "error" in result:
           return create_api_response(
               'Failed',
               message='Failed to calculate evaluation metrics.',
               error=result.get("error", "Ragas evaluation returned null")
           )
       data = {mode: {metric: result[i][metric] for metric in result[i]} for i, mode in enumerate(mode_list)}
       return create_api_response('Success', data=data)
   except Exception as e:
       logging.exception(f"Error while calculating evaluation metrics: {e}")
       return create_api_response(
           'Failed',
           message="Error while calculating evaluation metrics",
           error=str(e)
       )
   finally:
       gc.collect()

@app.post("/fetch_chunktext")
async def fetch_chunktext(
   uri: str = Form(None),
   database: str = Form(None),
   userName: str = Form(None),
   password: str = Form(None),
   document_name: str = Form(),
   page_no: int = Form(1),
   email=Form(None)
):
   try:
       start = time.time()
       result = await asyncio.to_thread(
           get_chunktext_results,
           uri=uri,
           username=userName,
           password=password,
           database=database,
           document_name=document_name,
           page_no=page_no
       )
       end = time.time()
       elapsed_time = end - start
       json_obj = {
           'api_name': 'fetch_chunktext',
           'db_url': uri,
           'userName': userName,
           'database': database,
           'document_name': document_name,
           'page_no': page_no,
           'logging_time': formatted_time(datetime.now(timezone.utc)),
           'elapsed_api_time': f'{elapsed_time:.2f}',
           'email': email
       }
       logger.log_struct(json_obj, "INFO")
       return create_api_response('Success', data=result, message=f"Total elapsed API time {elapsed_time:.2f}")
   except Exception as e:
       job_status = "Failed"
       message = "Unable to get chunk text response"
       error_message = str(e)
       logging.exception(f'Exception in fetch_chunktext: {error_message}')
       return create_api_response(job_status, message=message, error=error_message)
   finally:
       gc.collect()


@app.post("/backend_connection_configuration")
async def backend_connection_configuration():
    try:
        start = time.time()
        uri = os.getenv('NEO4J_URI')
        username= os.getenv('NEO4J_USERNAME')
        database= os.getenv('NEO4J_DATABASE')
        password= os.getenv('NEO4J_PASSWORD')
        gcs_file_cache = os.environ.get('GCS_FILE_CACHE')
        if all([uri, username, database, password]):
            graph = Neo4jGraph()
            logging.info(f'login connection status of object: {graph}')
            if graph is not None:
                graph_connection = True        
                graphDb_data_Access = graphDBdataAccess(graph)
                result = graphDb_data_Access.connection_check_and_get_vector_dimensions(database)
                result['gcs_file_cache'] = gcs_file_cache
                result['uri'] = uri
                end = time.time()
                elapsed_time = end - start
                result['api_name'] = 'backend_connection_configuration'
                result['elapsed_api_time'] = f'{elapsed_time:.2f}'
                result['graph_connection'] = f'{graph_connection}',
                result['connection_from'] = 'backendAPI'
                logger.log_struct(result, "INFO")
                return create_api_response('Success',message=f"Backend connection successful",data=result)
        else:
            graph_connection = False
            return create_api_response('Success',message=f"Backend connection is not successful",data=graph_connection)
    except Exception as e:
        graph_connection = False
        job_status = "Failed"
        message="Unable to connect backend DB"
        error_message = str(e)
        logging.exception(f'{error_message}')
        return create_api_response(job_status, message=message, error=error_message.rstrip('.') + ', or fill from the login dialog.', data=graph_connection)
    finally:
        gc.collect()
    
@app.post("/schema_visualization")
async def get_schema_visualization(uri=Form(None), userName=Form(None), password=Form(None), database=Form(None)):
    try:
        start = time.time()
        result = await asyncio.to_thread(visualize_schema,
           uri=uri,
           userName=userName,
           password=password,
           database=database)
        if result:
            logging.info("Graph schema visualization query successful")
        end = time.time()
        elapsed_time = end - start
        logging.info(f'Schema result from DB: {result}')
        json_obj = {'api_name':'schema_visualization','db_url':uri, 'userName':userName, 'database':database, 'logging_time': formatted_time(datetime.now(timezone.utc)), 'elapsed_api_time':f'{elapsed_time:.2f}'}
        logger.log_struct(json_obj, "INFO")
        return create_api_response('Success', data=result,message=f"Total elapsed API time {elapsed_time:.2f}")
    except Exception as e:
        message="Unable to get schema visualization from neo4j database"
        error_message = str(e)
        logging.info(message)
        logging.exception(f'Exception:{error_message}')
        return create_api_response("Failed", message=message, error=error_message)
    finally:
        gc.collect()

if __name__ == "__main__":
    uvicorn.run(app)