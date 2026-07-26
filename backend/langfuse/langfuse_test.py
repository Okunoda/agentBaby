from langfuse import Langfuse,observe,propagate_attributes
import uuid

uuid_h = uuid.uuid4().hex
print(uuid_h,len(uuid_h))

lf = Langfuse(
    secret_key="sk-lf-113f733d-fa1d-444c-8184-94fb5dab09af",
    public_key="pk-lf-279ab6b0-21f2-4422-8ceb-7a7384302bcb",
    host="http://localhost:3000"
)


@observe
def say_hi(name :str) -> str:
    with propagate_attributes(user_id="111",
                              session_id= "session_1",
                              metadata={"metadata_key1":"v1"},
                              tags=["tag1"],
                              ):


        lf.update_current_generation(
            model="deepseek",
            cost_details={"input":1,"cache_read_input_tokens":2, "output":3,"total":99},
            usage_details={
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
                "fake_tokens":999,
                "input":23000,
                "output":24000,
                "total":999
            }
        )
        return f"Hello {name}!"

say_hi("step1",langfuse_trace_id = uuid_h)
say_hi("step2",langfuse_trace_id = uuid_h)
